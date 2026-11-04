"""Milestone 3: Sentinel-2 acquisition robustness, e-mail safety, scope/status, run-log readiness, DW tiling/cache, diagnose script.

All remote services are STAND-INS (local COGs, fake transports/clients, synthetic fixtures). These tests prove logic only;
they are not evidence that any live service behaves this way."""
import dataclasses
import json
from datetime import datetime, timezone

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

import cs.imagery as imagery
from cs import scope
from cs.db import CaseStore
from cs.evidence.dynamic_world import CachedDWClient, DW_BANDS, DWComposite, split_grid
from cs.geo import grid_for_geometry
from cs.imagery import LiveProvider, NotConfigured
from cs.integrations.emailer import guarded_send
from cs.workflow import execute_encroachment
from tests.test_stabilization import reg  # noqa: F401  (fixture)

START, END = datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2024, 6, 1, tzinfo=timezone.utc)


# ------------------------------------------------------------------ Sentinel-2 acquisition
@pytest.fixture
def s2env(settings, tmp_path):
    grid = grid_for_geometry(box(78.40, 17.40, 78.41, 17.41), 4326, 10.0, pad_m=100)
    t = grid.transform
    tr = from_origin(t.c - 500, t.f + 500, 10.0, 10.0)
    for n, v, dt in (("g", 1500, "uint16"), ("s", 1100, "uint16"), ("scl", 4, "uint8")):
        with rasterio.open(tmp_path / f"{n}.tif", "w", driver="GTiff", height=grid.height + 100, width=grid.width + 100, count=1, dtype=dt,
                           crs=grid.crs, transform=tr) as d:
            d.write(np.full((grid.height + 100, grid.width + 100), v, dt), 1)

    def feat(i, cloud=1.0, green=None):
        return {"id": f"TEST_43QHV_2023{i:02d}01", "properties": {"datetime": f"2023-{i:02d}-01T05:00:00Z", "eo:cloud_cover": cloud,
                "grid:code": "MGRS-43QHV", "s2:processing_baseline": "05.10"},
                "assets": {"green": {"href": green or str(tmp_path / "g.tif")}, "swir16": {"href": str(tmp_path / "s.tif")},
                           "scl": {"href": str(tmp_path / "scl.tif")}}}
    prov = LiveProvider(dataclasses.replace(settings, mode="live"))
    return grid, prov, feat, tmp_path


def test_failed_item_does_not_stop_the_rest_and_partial_cache_triggers_refetch(s2env, monkeypatch):
    grid, prov, feat, tmp = s2env
    items = [feat(1), feat(2, green=str(tmp / "missing.tif")), feat(3), feat(4, cloud=80.0)]
    calls = []
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: calls.append(1) or items)
    scenes = prov.s2_scenes_for_grid(grid, "k", START, END)
    a = prov.last_acquisition
    assert len(scenes) == 2 and a["status"] == "partial"                      # item 2 failed, items 1 and 3 still read
    assert (a["found"], a["cloud_filtered"], a["read_ok"], a["failed"]) == (4, 1, 2, 1) and a["errors"][0]["id"].endswith("20230201")
    # the old bug: a non-empty cache was treated as complete. Now a manifest with failures forces a new query.
    items[1] = feat(2)
    assert len(prov.s2_scenes_for_grid(grid, "k", START, END)) == 3 and len(calls) == 2
    assert prov.last_acquisition["status"] == "complete" and prov.last_acquisition["failed"] == 0
    # a complete, matching cache is served without a query; refresh=True forces one
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: pytest.fail("complete cache must not re-query"))
    assert len(prov.s2_scenes_for_grid(grid, "k", START, END)) == 3
    assert prov.last_acquisition["from_cache_without_query"] is True
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: calls.append(1) or items)
    prov.s2_scenes_for_grid(grid, "k", START, END, refresh=True)
    assert len(calls) == 3


def test_cache_without_manifest_or_with_other_request_is_not_complete(s2env, monkeypatch):
    grid, prov, feat, tmp = s2env
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: [feat(1), feat(2)])
    prov.s2_scenes_for_grid(grid, "k", START, END)
    d = prov.settings.db_path.parent / "cache" / "k"
    seen = []
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: seen.append(1) or [feat(1), feat(2)])
    prov.s2_scenes_for_grid(grid, "k", START, END, max_cloud=10.0)             # different cloud limit -> new query
    (d / "manifest.json").unlink()
    prov.s2_scenes_for_grid(grid, "k", START, END, max_cloud=10.0)             # legacy single-scene caches have no manifest -> new query
    assert len(seen) == 2


def test_all_items_failing_raises_with_counts(s2env, monkeypatch):
    grid, prov, feat, tmp = s2env
    bad = str(tmp / "nope.tif")
    monkeypatch.setattr(prov, "_stac_search", lambda *a, **k: [feat(1, green=bad), feat(2, green=bad)])
    with pytest.raises(NotConfigured, match="failed 2"):
        prov.s2_scenes_for_grid(grid, "k", START, END)
    assert prov.last_acquisition["failed"] == 2


def test_search_follows_next_links(s2env, monkeypatch):
    grid, prov, feat, tmp = s2env
    pages = [{"features": [feat(1)], "links": [{"rel": "next", "method": "POST", "href": "https://x/p2", "body": {"page": 2}}]},
             {"features": [feat(2)], "links": []}]
    seen = []

    class R:
        def __init__(self, p): self.p = p
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self.p).encode()
    monkeypatch.setattr(imagery.urllib.request, "urlopen", lambda req, timeout=0: seen.append(req.full_url) or R(pages[len(seen) - 1]))
    out = prov._stac_search((78.4, 17.4, 78.41, 17.41), START, END, 20.0, 100)
    assert [f["id"][-8:] for f in out] == ["20230201", "20230101"] and seen[1] == "https://x/p2"


# ------------------------------------------------------------------ e-mail safety
def _case(cid="ENC-9001", state="potential_change_detected", status="under_review", synthetic=0, kind="registry_waterbody_buffer"):
    return {"case_id": cid, "state": state, "status": status, "synthetic": synthetic, "evidence": json.dumps({"aoi": {"kind": kind}})}


class _Sender:
    def __init__(self): self.calls = []
    def __call__(self, case, pdf, recipient, extra): self.calls.append(recipient); return True, "sent"


@pytest.mark.parametrize("kw,why", [
    (dict(synthetic=1), "synthetic"), (dict(kind="fixed_analysis_grid"), "legacy"),
    (dict(state="not_assessed"), "not assessed"), (dict(state="no_change_detected"), "no change"),
    (dict(status="potential"), "not been reviewed")])
def test_email_blocked_for_unsuitable_cases_and_attempt_is_audited(settings, monkeypatch, kw, why):
    monkeypatch.setenv("CS_ALERT_TO", "someone@example.org")
    st, s = CaseStore(settings.db_path), _Sender()
    ok, msg = guarded_send(st, _case(**kw), "me", "officer@example.gov.in", True, None, sender=s)
    assert not ok and why in msg and s.calls == []
    assert st.audit("ENC-9001")[-1]["action"] == "report_email_blocked"


def test_email_needs_typed_recipient_and_confirmation_and_never_defaults(settings, monkeypatch):
    monkeypatch.setenv("CS_ALERT_TO", "default@example.org")
    st, s = CaseStore(settings.db_path), _Sender()
    assert not guarded_send(st, _case(), "me", "", True, None, sender=s)[0]            # no recipient -> no default is used
    assert not guarded_send(st, _case(), "me", "not-an-address", True, None, sender=s)[0]
    assert not guarded_send(st, _case(), "me", "officer@example.gov.in", False, None, sender=s)[0]
    assert s.calls == []
    ok, _ = guarded_send(st, _case(), "me", " officer@example.gov.in ", True, None, sender=s)
    assert ok and s.calls == ["officer@example.gov.in"]
    assert [a["action"] for a in st.audit("ENC-9001")] == ["report_email_blocked"] * 3 + ["report_email_sent"]


def test_app_has_no_prefilled_officer_address():
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("app.py").read_text()
    assert "collector_hyd" not in src and "telangana.gov.in\"" not in src.split("officer_email")[1][:200]


def test_automatic_alert_is_skipped_for_synthetic_cases(settings, monkeypatch):
    for k, v in (("CS_SMTP_HOST", "smtp.invalid"), ("CS_SMTP_FROM", "a@b.c"), ("CS_ALERT_TO", "x@y.z")):
        monkeypatch.setenv(k, v)
    import cs.workflow as wf
    monkeypatch.setattr(wf, "send_case_alert", lambda *a, **k: pytest.fail("synthetic cases must never be e-mailed"))
    _, cid = execute_encroachment(settings, "persistent_loss")
    assert "email_alert_skipped" in [a["action"] for a in CaseStore(settings.db_path).audit(cid)]


# ------------------------------------------------------------------ scope, status, dashboard counts
def _stub_live_fixed_grid_sources(monkeypatch):
    from cs.imagery import LiveProvider
    from cs.evidence.base import Unavailable
    import cs.workflow as wf

    def local_scenes(self, grid, cache_key, start, end, **kwargs):
        scenes = [s for s in self._load_cached(self._cache_dir(cache_key), grid=grid) if start <= s.acquired <= end]
        self.last_acquisition = {"status": "complete", "found": len(scenes), "cloud_filtered": 0,
                                 "read_ok": len(scenes), "failed": 0, "scenes_in_window": len(scenes)}
        return scenes

    monkeypatch.setattr(LiveProvider, "s2_scenes_for_grid", local_scenes)
    monkeypatch.setattr(wf, "acquire_evidence", lambda *a, **k: {
        key: Unavailable(key, "isolated test source") for key in ("sentinel1", "dynamic_world", "jrc_gsw")})


def test_scope_and_display_status_without_touching_stored_rows(settings, monkeypatch):
    _stub_live_fixed_grid_sources(monkeypatch)
    _, syn = execute_encroachment(settings, "sparse")                                   # not_assessed, synthetic
    _, leg = execute_encroachment(dataclasses.replace(settings, mode="live"), "", study_area="pallikaranai_marsh")        # legacy grid, cached real scenes
    st = CaseStore(settings.db_path)
    a, b = st.get_case(syn), st.get_case(leg)
    assert scope.case_scope(a) == scope.SYNTHETIC and scope.case_scope(b) == scope.LEGACY
    assert a["status"] == "potential" and scope.display_status(a) == "unassessed"       # stored value unchanged
    assert scope.display_status({**b, "state": "no_change_detected"}) == "no change detected - screening only"
    assert scope.display_status({**b, "state": "analysis_failed"}) == "analysis failed"
    assert scope.display_status({**b, "state": "potential_change_detected"}) == "potential"
    assert scope.display_status({**a, "status": "under_review"}) == "under_review"
    assert scope.filter_scope([a, b], scope.PILOT) == [] and scope.filter_scope([a, b], scope.LEGACY) == [b]
    assert not scope.awaiting_review(b) and not scope.usable_evidence(b)
    pilot = _case()
    assert scope.case_scope(pilot) == scope.PILOT and scope.usable_evidence(pilot)
    assert scope.awaiting_review({**pilot, "status": "potential"}) and not scope.awaiting_review(pilot)
    assert not scope.awaiting_review({**pilot, "status": "potential", "state": "not_assessed"})


def test_dashboard_counts_exclude_synthetic_and_legacy(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    _stub_live_fixed_grid_sources(monkeypatch)
    monkeypatch.setenv("CS_DB_PATH", str(tmp_path / "d.db")); monkeypatch.setenv("CS_OUTPUT_DIR", str(tmp_path / "o"))
    from cs.config import Settings
    s = Settings.from_env()
    execute_encroachment(s, "persistent_loss"); execute_encroachment(dataclasses.replace(s, mode="live"), "", study_area="pallikaranai_marsh")
    at = AppTest.from_file("../app.py", default_timeout=120).run()
    assert not at.exception
    m = {x.label: x.value for x in at.metric}
    assert m["Cases recorded (this scope)"] == "0" and m["Awaiting review"] == "0" and m["Assessments with usable evidence"] == "0"
    at.radio(key="dash_scope").set_value("synthetic").run()
    m = {x.label: x.value for x in at.metric}
    assert m["Cases recorded (this scope)"] == "1" and m["Awaiting review"] == "n/a"


# ------------------------------------------------------------------ run log and generated readiness
class _Reg:
    district = None
    waterbodies = []


def test_readiness_is_generated_from_the_run_log(settings):
    st = CaseStore(settings.db_path)
    rows = {r["item"]: r for r in scope.readiness_rows(_Reg(), {}, st)}
    assert rows["Sentinel-2 L2A (live, Earth Search)"]["last_run"] == "no recorded run"
    assert rows["Sentinel-2 L2A (live, Earth Search)"]["tested_against_remote"] == "no"
    st.record_source_run("sentinel2", "lake", "partial", {"found": 4, "failed": 1}, "item x failed")
    r = {r["item"]: r for r in scope.readiness_rows(_Reg(), {}, st)}["Sentinel-2 L2A (live, Earth Search)"]
    assert r["last_run"].startswith("exercised ") and "partial" in r["last_run"] and "found=4" in r["last_run"]
    assert r["tested_against_remote"] == "no"                                          # partial is not a success
    st.record_source_run("sentinel1", "lake", "succeeded", {"n_scenes": 3}, standin=True)
    assert {r["item"]: r for r in scope.readiness_rows(_Reg(), {}, st)}["Sentinel-1 RTC"]["tested_against_remote"] == "no"   # stand-in run
    st.record_source_run("sentinel1", "lake", "succeeded", {"n_scenes": 3})
    assert {r["item"]: r for r in scope.readiness_rows(_Reg(), {}, st)}["Sentinel-1 RTC"]["tested_against_remote"] == "yes"
    with pytest.raises(ValueError):
        st.record_source_run("sentinel1", "lake", "ok")


def test_registry_workflow_writes_run_log_marked_standin(settings, reg):
    from tests.test_milestone2_pipeline import _run, make_transport, PlanetaryComputer
    registry, wb = reg
    s, (res, cid) = _run(settings, registry, wb, pc=PlanetaryComputer("u", "s", transport=make_transport(fail=True)), dw_client=None)
    runs = CaseStore(settings.db_path).source_runs()
    by = {r["source"]: r for r in runs}
    assert set(by) >= {"sentinel2", "sentinel1", "dynamic_world", "jrc_gsw"}
    assert all(r["standin"] == 1 for r in runs) and all(r["case_id"] == cid for r in runs)
    assert by["dynamic_world"]["outcome"] in ("unavailable", "failed") and by["sentinel1"]["outcome"] != "succeeded"
    rr = {r["item"]: r for r in scope.readiness_rows(registry, {}, CaseStore(settings.db_path))}
    assert all(r.get("tested_against_remote") != "yes" for r in rr.values())          # stand-ins never count as remote tests


def test_failed_acquisition_is_logged_before_the_error_propagates(settings, reg):
    from tests.test_stabilization import _FixtureProvider
    from cs.workflow import execute_encroachment_for_waterbody
    registry, wb = reg
    with pytest.raises(NotConfigured):
        execute_encroachment_for_waterbody(settings, "lake", registry=registry, provider=_FixtureProvider(settings, wb, fail=True))
    r = CaseStore(settings.db_path).source_runs("sentinel2")
    assert r and r[0]["outcome"] == "failed" and "no imagery" in r[0]["error"]


# ------------------------------------------------------------------ Dynamic World tiling and cache
def test_split_grid_tiles_cover_the_grid_exactly_and_align():
    g = grid_for_geometry(box(78.40, 17.40, 78.43, 17.43), 4326, 10.0, pad_m=0)
    tiles = list(split_grid(g, 40_000))
    assert len(tiles) > 1 and sum(t.height * t.width for _, _, t in tiles) == g.height * g.width
    for r0, c0, t in tiles:
        assert t.height * t.width <= 40_000 + 1 and t.transform.c == pytest.approx(g.transform.c + c0 * g.transform.a)
        assert t.transform.f == pytest.approx(g.transform.f + r0 * g.transform.e)


class _InnerDW:
    def __init__(self): self.n = 0
    def available(self): return True, ""
    def composite(self, grid, start, end):
        self.n += 1
        p = {b: np.full((grid.height, grid.width), .1, "float32") for b in DW_BANDS}
        return DWComposite(p, np.full((grid.height, grid.width), 4, "int16"), 4, ["a", "b", "c", "d"], (start, end), "a", "d", {"k": 1})


def test_dw_cache_is_keyed_on_grid_and_window(tmp_path):
    g = grid_for_geometry(box(78.40, 17.40, 78.41, 17.41), 4326, 10.0, pad_m=0)
    inner = _InnerDW(); c = CachedDWClient(inner, tmp_path)
    a = c.composite(g, START, END); b = c.composite(g, START, END)
    assert inner.n == 1 and b.meta.get("from_cache") and b.n_images == 4 and np.allclose(a.probs["water"], b.probs["water"])
    c.composite(g, START, datetime(2024, 7, 1, tzinfo=timezone.utc))
    assert inner.n == 2


# ------------------------------------------------------------------ diagnose script
def test_diagnose_output_shape_has_no_secrets(tmp_path):
    from scripts import diagnose_live as D
    from cs.config import Settings
    item = {"id": "i1", "properties": {"datetime": "2024-01-01T00:00:00Z", "sar:polarizations": ["VV", "VH"], "sat:orbit_state": "ascending",
                                       "sat:relative_orbit": 19, "sar:instrument_mode": "IW", "eo:cloud_cover": 3},
            "assets": {"vv": {"href": "https://x/vv.tif"}, "green": {"href": "https://x/g.tif"}, "occurrence": {"href": "https://x/o.tif"}}}

    def http(url, body=None, headers=None, timeout=0):
        if url.endswith("/api/sas/v1/token/jrc-gsw"):
            return {"token": "SECRET-TOKEN-123", "msft:expiry": "2030-01-01T00:00:00Z"}
        if "/collections/" in url:
            return {"extent": {"temporal": {"interval": [["1984-03-16T00:00:00Z", "2021-12-31T23:59:59Z"]]}}}
        if url.endswith("/stac/v1") or url.endswith("/stac/v1/"):
            return {"id": "microsoft-pc", "stac_version": "1.0.0"}
        return {"features": [item], "links": []}
    s = dataclasses.replace(Settings(db_path=tmp_path / "x.db", output_dir=tmp_path), gee_project="")
    res = D.run([78.4, 17.4, 78.41, 17.41], settings=s, http=http, reader=lambda href: None, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
    c = res["checks"]
    assert set(c) == {"earth_search_sentinel2", "planetary_computer_stac", "planetary_computer_sas", "jrc_gsw", "sentinel1_rtc", "earth_engine_dynamic_world"}
    assert c["earth_search_sentinel2"]["found"] == 1 and c["earth_search_sentinel2"]["readable"] == 1
    assert c["sentinel1_rtc"]["first_item"]["sar"]["sat:orbit_state"] == "ascending" and c["jrc_gsw"]["collection_temporal_extent"]
    assert c["planetary_computer_sas"]["token_present"] is True
    assert c["earth_engine_dynamic_world"]["ok"] is False and "CS_GEE_PROJECT" in c["earth_engine_dynamic_world"]["error"]
    assert "SECRET-TOKEN-123" not in json.dumps(res)
    out = tmp_path / "diagnostics.json"
    assert D.main(["--bbox", "78.4", "17.4", "78.41", "17.41", "--out", str(out), "--timeout", "1"]) == 0     # real calls fail fast and are recorded
    assert json.loads(out.read_text())["checks"]["planetary_computer_stac"]["ok"] in (True, False)
