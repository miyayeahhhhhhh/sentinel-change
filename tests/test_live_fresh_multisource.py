import dataclasses
import json
from datetime import datetime, timezone, timedelta

import numpy as np

from cs.evidence import acquire as acquisition
from cs.evidence.base import EvidenceRequest, Unavailable
from cs.evidence.dynamic_world import CachedDWClient, DW_BANDS, DWComposite
from cs.evidence.stac import PlanetaryComputer
from cs.geo import Grid
from cs.imagery import LiveProvider
from cs.models import State
from cs import workflow
from rasterio.crs import CRS
from rasterio.transform import from_origin


def test_sentinel2_search_follows_all_pages_and_orders_newest_first(settings, tmp_path, monkeypatch):
    pages = []
    for i in range(12):
        links = ([{"rel": "next", "method": "POST", "href": "https://stac.test/search", "body": {"cursor": i + 1}}]
                 if i < 11 else [])
        pages.append({"features": [{"id": f"scene-{i}", "properties": {"datetime": f"2026-{(i % 12) + 1:02d}-01T00:00:00Z"}}], "links": links})
    calls = []

    class Response:
        def __init__(self, page): self.page = page
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(self.page).encode()

    monkeypatch.setattr("cs.imagery.urllib.request.urlopen",
                        lambda req, timeout=0: calls.append(req.full_url) or Response(pages[len(calls) - 1]))
    provider = LiveProvider(dataclasses.replace(settings, mode="live"))
    found = provider._stac_search((78, 17, 79, 18), datetime(2023, 1, 1, tzinfo=timezone.utc),
                                  datetime(2026, 11, 5, tzinfo=timezone.utc), 20, 100)
    assert len(found) == 12 and len(calls) == 12
    assert found[0]["id"] == "scene-11" and found[-1]["id"] == "scene-0"


def test_planetary_computer_search_is_not_truncated_at_ten_pages():
    pages, calls = [], []
    for i in range(12):
        links = ([{"rel": "next", "method": "POST", "href": "https://pc.test/search", "body": {"cursor": i + 1}}]
                 if i < 11 else [])
        pages.append({"features": [{"id": str(i), "properties": {"datetime": f"2026-{(i % 12) + 1:02d}-01T00:00:00Z"}}], "links": links})

    def transport(url, body=None, headers=None, timeout=30):
        calls.append(url)
        return pages[len(calls) - 1]

    pc = PlanetaryComputer("https://pc.test", "https://pc.test/sas", transport=transport)
    results = pc.search("sentinel-1-rtc", (78, 17, 79, 18), limit=100)
    assert len(results) == 12 and len(calls) == 12
    assert results[0]["id"] == "11"


def test_force_refresh_bypasses_cached_dynamic_world_composite(tmp_path):
    class Inner:
        calls = 0
        def available(self): return True, ""
        def composite(self, grid, start, end):
            self.calls += 1
            probs = {band: np.zeros((2, 2), dtype="float32") for band in DW_BANDS}
            return DWComposite(probs, np.ones((2, 2), dtype="int16"), 3, [str(self.calls)], (start, end))

    inner = Inner()
    cache = CachedDWClient(inner, tmp_path, force_refresh=True)
    grid = Grid(from_origin(500000, 2000000, 10, 10), CRS.from_epsg(32644), 2, 2)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 5, tzinfo=timezone.utc)
    cache.composite(grid, start, end)
    cache.composite(grid, start, end)
    assert inner.calls == 2


def test_live_fixed_grid_uses_fresh_optical_and_all_evidence_sources(settings, monkeypatch):
    live_settings = dataclasses.replace(settings, mode="live")
    calls = {}

    class Provider:
        last_acquisition = {"status": "complete", "found": 3, "cloud_filtered": 0, "read_ok": 3,
                            "failed": 0, "scenes_in_window": 3}
        def s2_scenes_for_grid(self, grid, cache_key, start, end, **kwargs):
            calls["window"] = (start, end)
            calls["cache_key"] = cache_key
            calls["refresh"] = kwargs["refresh"]
            return ["current catalog scenes"]

    evidence = {name: Unavailable(name, "not available in isolated test")
                for name in ("sentinel1", "dynamic_world", "jrc_gsw")}

    def acquire(settings, req, **kwargs):
        calls["evidence_refresh"] = kwargs["refresh"]
        calls["evidence_window"] = req
        return evidence

    monkeypatch.setattr(workflow, "get_provider", lambda settings: Provider())
    monkeypatch.setattr(workflow, "acquire_evidence", acquire)
    monkeypatch.setattr(workflow, "run_encroachment",
                        lambda scenes, grid, params, **kwargs: (scenes, kwargs))
    monkeypatch.setattr(workflow, "_register", lambda settings, result: (result, "ENC-TEST"))

    result, case_id = workflow.execute_encroachment(live_settings, "", study_area="pallikaranai_marsh")
    assert case_id == "ENC-TEST"
    assert calls["refresh"] is True and calls["evidence_refresh"] is True
    assert calls["evidence_window"].aoi_id == "pallikaranai_marsh"
    assert set(result[1]["evidence"]) == {"sentinel1", "dynamic_world", "jrc_gsw"}
    assert calls["window"][1] > datetime.now(timezone.utc) - timedelta(seconds=5)
