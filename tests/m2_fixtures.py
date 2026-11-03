"""TEST-ONLY fixtures for Milestone 2. Everything here is SYNTHETIC or a stand-in for a remote service; none of it
is satellite data and none of it proves the real services work."""
from datetime import datetime, timedelta, timezone

import numpy as np

from cs.evidence.dynamic_world import DW_BANDS, DWComposite
from cs.evidence.stac import PlanetaryComputer
from cs.geo import polygon_mask

AS_OF = datetime(2026, 10, 31, tzinfo=timezone.utc)
RECENT_START = AS_OF - timedelta(days=365)
START = RECENT_START - timedelta(days=3 * 365)


def lake_masks(wb, grid):
    water = polygon_mask(wb, 4326, grid)
    col = int(np.nonzero(water.any(0))[0].mean())
    east = water.copy(); east[:, :col] = False          # eastern half of the lake is "lost" in the recent window
    return water, east


def s1_items(orbits=(("ascending", 125, 1.0),), step_days=30, bad=()):
    """STAC-like Sentinel-1 RTC items. orbits: (state, relative_orbit, share of dates kept)."""
    items, t = [], START
    while t < AS_OF:
        for k, (state, rel, share) in enumerate(orbits):
            if (int(t.timestamp()) // 86400 + k) % 100 >= share * 100:
                continue
            tt = t + timedelta(hours=k)
            items.append({"id": f"S1-{state[:1]}{rel}-{tt:%Y%m%d%H}", "properties": {
                "datetime": tt.strftime("%Y-%m-%dT%H:%M:%SZ"), "sar:polarizations": ["VV", "VH"], "sar:instrument_mode": "IW",
                "sat:orbit_state": state, "sat:relative_orbit": rel, "platform": "sentinel-1a", "sar:product_type": "RTC"},
                "assets": {"vv": {"href": f"mem://S1-{state[:1]}{rel}-{tt:%Y%m%d%H}"}}})
        t += timedelta(days=step_days)
    return items


def make_s1_reader(wb, recent_loss=True, fail_ids=()):
    calls = []

    def reader(href, grid, resampling="bilinear", *a, **k):
        sid = href.split("://")[1].split("?")[0]
        calls.append(sid)
        if sid in fail_ids:
            raise OSError("simulated read failure")
        water, east = lake_masks(wb, grid)
        date = datetime.strptime(sid.split("-")[-1], "%Y%m%d%H").replace(tzinfo=timezone.utc)
        wet = water & ~east if (recent_loss and date >= RECENT_START) else water
        rng = np.random.default_rng(abs(hash(sid)) % (2 ** 32))
        lin = np.where(wet, 10 ** (-2.2), 10 ** (-1.0)) * rng.gamma(16, 1 / 16, water.shape)
        return lin.astype("float32")
    reader.calls = calls
    return reader


def make_transport(s1=None, jrc_items=None, fail=False):
    log = []

    def transport(url, body=None, headers=None, timeout=30):
        log.append((url, body))
        if fail:
            from cs.evidence.stac import RemoteError
            raise RemoteError("simulated outage")
        if "/sas/" in url:
            return {"token": "sig=TESTTOKEN", "msft:expiry": "2099-01-01T00:00:00Z"}
        coll = body["collections"][0]
        feats = (s1 or []) if coll == "sentinel-1-rtc" else (jrc_items or [])
        return {"features": feats, "links": []}
    transport.log = log
    return transport


def fake_pc(**kw):
    return PlanetaryComputer("https://stac.test/v1", "https://stac.test/sas", transport=make_transport(**kw))


def jrc_item():
    return {"id": "jrc-gsw-TEST-tile", "properties": {"start_datetime": "1984-03-01T00:00:00Z", "end_datetime": "2021-12-31T23:59:59Z"},
            "assets": {"occurrence": {"href": "mem://occ"}, "seasonality": {"href": "mem://seas"}}}


def make_jrc_reader(wb, seasonal=False, cover=True):
    def reader(href, grid, resampling="nearest", band=1, return_mask=False):
        water, _ = lake_masks(wb, grid)
        occ_v, seas_v = (30, 4) if seasonal else (90, 12)
        data = np.where(water, occ_v if "occ" in href else seas_v, 0).astype("uint8")
        ok = np.ones(water.shape, bool) if cover else np.zeros(water.shape, bool)
        return (data, ok) if return_mask else data
    return reader


class FakeDW:
    """Local stand-in for the Earth Engine client."""
    def __init__(self, wb, mode="built", n_images=6, n_obs=5):
        self.wb, self.mode, self.n_images, self.n_obs = wb, mode, n_images, n_obs
        self.calls = []

    def available(self):
        return True, ""

    def composite(self, grid, start, end):
        self.calls.append((start, end))
        water, east = lake_masks(self.wb, grid)
        recent = start >= RECENT_START
        lost = east if recent else np.zeros_like(east)
        H, W = water.shape
        p = {b: np.full((H, W), 0.02, "float32") for b in DW_BANDS}
        p["water"] = np.where(water & ~lost, 0.9, 0.03).astype("float32")
        if self.mode == "water_persists":
            p["water"] = np.where(water, 0.9, 0.03).astype("float32")
        dest = {"built": ("built", .7), "bare": ("bare", .7), "veg": ("grass", .7)}.get(self.mode, ("built", .7))
        p[dest[0]] = np.where(lost, dest[1], 0.02).astype("float32")
        n = np.full((H, W), self.n_obs, "int16")
        return DWComposite(p, n, self.n_images, [f"DW-{start:%Y%m%d}-{i}" for i in range(self.n_images)], (start, end), "a", "b", {})
