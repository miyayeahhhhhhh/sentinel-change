"""Read-only live-service diagnostic. Run locally:  python scripts/diagnose_live.py --aoi hussain-sagar

Checks (short timeouts, no secrets printed, nothing written except diagnostics.json):
  * Earth Search Sentinel-2 L2A: items found for the AOI/window, and whether the first few green bands are readable
  * Planetary Computer: STAC reachable, SAS token obtainable (only 'ok' is recorded, never the token)
  * jrc-gsw: item and asset names, temporal coverage reported by the service
  * sentinel-1-rtc: item count for the AOI/window and the STAC property/asset names of the first item
  * Earth Engine: initialisation and Dynamic World image count over the AOI
Paste diagnostics.json back to have the adapters corrected against what the services actually return.
Nothing here is evidence about the AOI; it only verifies service assumptions.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cs.config import Settings                      # noqa: E402
from cs.evidence.stac import PlanetaryComputer, http_json   # noqa: E402
from cs.registry import Registry                    # noqa: E402

ES_URL = "https://earth-search.aws.element84.com/v1"


def _step(out: dict, name: str, fn):
    try:
        out[name] = {"ok": True, **fn()}
    except Exception as exc:                       # a failed check is a result, not a crash
        out[name] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def _shape(feature: dict) -> dict:
    p = feature.get("properties", {})
    return {"id": feature.get("id"), "datetime": p.get("datetime"), "start_datetime": p.get("start_datetime"),
            "end_datetime": p.get("end_datetime"), "asset_names": sorted(feature.get("assets", {})),
            "property_names": sorted(p), "sar": {k: p[k] for k in p if k.startswith(("sar:", "sat:"))}}


def run(aoi_bbox, months: int = 12, timeout: float = 20.0, http=http_json, settings: Settings | None = None,
        reader=None, ee_module=None, now: datetime | None = None) -> dict:
    settings = settings or Settings.from_env()
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=round(months * 30.4))
    out: dict = {"generated_at": end.isoformat(timespec="seconds"), "aoi_bbox": [round(v, 5) for v in aoi_bbox],
                 "window": [start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")], "checks": {}}
    c = out["checks"]
    pc = PlanetaryComputer(settings.pc_stac_url, settings.pc_sas_url, settings.pc_subscription_key, timeout, transport=http)

    def s2():
        body = {"collections": ["sentinel-2-l2a"], "bbox": list(aoi_bbox), "limit": 100,
                "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"}
        feats, url, pages = [], f"{ES_URL}/search", 0
        while url and pages < 10:
            r = http(url, body, None, timeout)
            feats += r.get("features", [])
            nxt = next((l for l in r.get("links", []) if l.get("rel") == "next" and l.get("method", "GET").upper() == "POST"), None)
            url, body = (nxt["href"], {**body, **(nxt.get("body") or {})}) if nxt else (None, body)
            pages += 1
        readable, failed = 0, []
        rd = reader or _default_reader
        for f in feats[:3]:
            try:
                rd(f["assets"]["green"]["href"]); readable += 1
            except Exception as exc:
                failed.append({"id": f.get("id"), "error": f"{type(exc).__name__}: {str(exc)[:150]}"})
        return {"found": len(feats), "probed": min(3, len(feats)), "readable": readable, "failed": failed,
                "cloud_cover_values": [f.get("properties", {}).get("eo:cloud_cover") for f in feats[:10]]}

    def pc_root():
        r = http(settings.pc_stac_url, None, None, timeout)
        return {"id": r.get("id"), "stac_version": r.get("stac_version")}

    def sas():
        r = http(f"{settings.pc_sas_url}/{settings.jrc_collection}", None, None, timeout)
        return {"token_present": bool(r.get("token")), "expiry": r.get("msft:expiry")}   # the token itself is never recorded

    def jrc():
        items = pc.search(settings.jrc_collection, aoi_bbox, limit=10, max_pages=1)
        coll = http(f"{settings.pc_stac_url}/collections/{settings.jrc_collection}", None, None, timeout)
        return {"items_found": len(items), "first_item": _shape(items[0]) if items else None,
                "collection_temporal_extent": (coll.get("extent") or {}).get("temporal"),
                "collection_summaries": coll.get("summaries"), "asset_names_expected": ["occurrence", "seasonality"]}

    def s1():
        items = pc.search(settings.s1_collection, aoi_bbox, start, end, limit=100, max_pages=10)
        return {"items_found": len(items), "first_item": _shape(items[0]) if items else None,
                "orbit_groups": sorted({f"{f['properties'].get('sat:orbit_state')}/{f['properties'].get('sat:relative_orbit')}" for f in items})[:20],
                "polarizations_seen": sorted({",".join(f["properties"].get("sar:polarizations", [])) for f in items})}

    def ee_dw():
        if not settings.gee_project:
            raise RuntimeError("CS_GEE_PROJECT is not set")
        ee = ee_module
        if ee is None:
            import ee            # noqa: F811
        if settings.gee_service_account and settings.gee_key_file:
            ee.Initialize(ee.ServiceAccountCredentials(settings.gee_service_account, settings.gee_key_file), project=settings.gee_project)
        else:
            ee.Initialize(project=settings.gee_project)
        w, s, e, n = aoi_bbox
        coll = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(ee.Geometry.Rectangle([w, s, e, n]))
                .filterDate(f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"))
        return {"initialised": True, "dynamic_world_images": int(coll.size().getInfo())}

    for name, fn in (("earth_search_sentinel2", s2), ("planetary_computer_stac", pc_root), ("planetary_computer_sas", sas),
                     ("jrc_gsw", jrc), ("sentinel1_rtc", s1), ("earth_engine_dynamic_world", ee_dw)):
        _step(c, name, fn)
    return out


def _default_reader(href: str):
    import rasterio
    with rasterio.Env(AWS_NO_SIGN_REQUEST="YES"), rasterio.open(href) as src:
        src.read(1, window=((0, 16), (0, 16)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--aoi", help="registry waterbody id (e.g. hussain-sagar)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"), help="explicit lon/lat bbox instead of --aoi")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--out", type=Path, default=Path("diagnostics.json"))
    a = ap.parse_args(argv)
    if a.bbox:
        bbox = a.bbox
    elif a.aoi:
        wb = Registry.load().waterbody(a.aoi)
        if wb.geometry is None:
            print(f"waterbody {a.aoi!r} has no usable geometry", file=sys.stderr)
            return 2
        bbox = list(wb.geometry.bounds)
    else:
        print("give --aoi <registry id> or --bbox W S E N", file=sys.stderr)
        return 2
    res = run(bbox, a.months, a.timeout)
    a.out.write_text(json.dumps(res, indent=2, default=str))
    for k, v in res["checks"].items():
        print(f"{'OK  ' if v['ok'] else 'FAIL'} {k}" + ("" if v["ok"] else f"  - {v['error'][:110]}"))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
