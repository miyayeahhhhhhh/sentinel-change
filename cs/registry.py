"""Provenance-tracked registry of the pilot district and waterbody/AOI records.

Nothing is "verified" because someone typed it: status is derived from the recorded source, a SHA-256 of the stored
file, the publisher's URL host, and geometry checks. No geometry is bundled; absent data is reported as a blocker.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import geopandas as gpd
import shapely
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from .config import ROOT
from .geo import utm_epsg_for_lonlat

DEFAULT_DIR = ROOT / "data" / "registry"
OFFICIAL_HOST_SUFFIXES = (".gov.in", ".nic.in")          # official Government of India / state domains
INDIA_LONLAT = (68.0, 6.0, 98.0, 38.0)                    # plausibility box (catches degrees/metres or axis mix-ups)
MIN_CONTAINMENT = 0.90                                    # waterbody share that must lie inside the verified district
RANK = {"invalid": 0, "unverified": 1, "provisional": 2, "verified": 3}
REQUIRED_SOURCE = ("publisher", "dataset", "url", "licence", "retrieved_utc")


@dataclass
class Assessment:
    status: str
    reasons: list = field(default_factory=list)

    def cap(self, status: str, reason: str) -> None:
        if RANK[status] < RANK[self.status]:
            self.status = status
        self.reasons.append(reason)


@dataclass
class GeoRecord:
    id: str
    name: str
    kind: str                         # "district" | "waterbody"
    geometry: BaseGeometry | None     # EPSG:4326, polygonal; None when unreadable
    source: dict
    sha256: str
    status: str
    reasons: list
    district_id: str | None = None
    containment: float | None = None
    repaired: bool = False

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def provenance(self) -> dict:
        return {"id": self.id, "name": self.name, "kind": self.kind, "status": self.status, "reasons": self.reasons,
                "source": self.source, "sha256": self.sha256, "containment_in_district": self.containment}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def assess_source(source: dict, file_path: Path, expected_sha: str | None) -> Assessment:
    a = Assessment("verified")
    missing = [k for k in REQUIRED_SOURCE if not str(source.get(k) or "").strip()]
    if missing:
        a.cap("unverified", f"source record incomplete: missing {', '.join(missing)}")
    host = (urlparse(str(source.get("url", ""))).hostname or "").lower()
    if host and not any(host.endswith(sfx) or host == sfx.lstrip(".") for sfx in OFFICIAL_HOST_SUFFIXES):
        a.cap("provisional", f"publisher URL host '{host}' is not an official government domain (third-party/derived)")
    elif not host:
        a.cap("unverified", "no usable source URL")
    try:
        r = date.fromisoformat(str(source.get("retrieved_utc"))[:10])
        if r > datetime.now(timezone.utc).date():
            a.cap("unverified", "retrieval date is in the future")
    except ValueError:
        a.cap("unverified", "retrieved_utc is not an ISO date")
    if not file_path.exists():
        a.cap("invalid", f"stored file missing: {file_path.name}")
    elif not expected_sha:
        a.cap("unverified", "no SHA-256 recorded for the stored file")
    elif sha256_file(file_path) != expected_sha:
        a.cap("invalid", "SHA-256 mismatch: stored file differs from the recorded checksum")
    return a


def read_polygon(entry: dict, base: Path) -> tuple[BaseGeometry | None, list, bool]:
    """Load, filter, reproject to EPSG:4326, repair and dissolve a registry geometry. Returns (geom, problems, repaired)."""
    problems, repaired = [], False
    try:
        gdf = gpd.read_file(base / entry["file"])
    except Exception as exc:
        return None, [f"unreadable geometry file: {exc}"], False
    if gdf.crs is None:
        if entry.get("crs"):
            gdf = gdf.set_crs(entry["crs"])
        else:
            return None, ["file has no CRS and none is declared in the manifest"], False
    flt = entry.get("feature_filter")
    if flt:
        if flt["field"] not in gdf.columns:
            return None, [f"filter field '{flt['field']}' not in file columns {list(gdf.columns)}"], False
        gdf = gdf[gdf[flt["field"]].astype(str).str.strip().str.casefold() == str(flt["value"]).strip().casefold()]
    if gdf.empty:
        return None, ["no feature matches the filter" if flt else "file has no features"], False
    gdf = gdf.to_crs(4326)
    geoms = []
    for g in gdf.geometry:
        if g is None or g.is_empty:
            continue
        if not g.is_valid:
            g, repaired = shapely.make_valid(g), True
        parts = [p for p in getattr(g, "geoms", [g]) if isinstance(p, (Polygon, MultiPolygon))]
        for p in parts:
            geoms.extend(p.geoms if isinstance(p, MultiPolygon) else [p])
    if not geoms:
        return None, ["no polygonal geometry"], repaired
    geom = shapely.union_all(geoms)
    w, s, e, n = geom.bounds
    if not (INDIA_LONLAT[0] <= w and e <= INDIA_LONLAT[2] and INDIA_LONLAT[1] <= s and n <= INDIA_LONLAT[3]):
        return None, [f"coordinates outside plausible range for India after reprojection (bounds {geom.bounds}); check the CRS"], repaired
    return geom, problems, repaired


def area_km2(geom: BaseGeometry) -> float:
    c = geom.centroid
    return float(gpd.GeoSeries([geom], crs=4326).to_crs(utm_epsg_for_lonlat(c.x, c.y)).area.iloc[0] / 1e6)


def _build(entry: dict, kind: str, base: Path) -> GeoRecord:
    a = assess_source(entry.get("source", {}), base / entry.get("file", "_missing_"), entry.get("sha256"))
    geom, problems, repaired = read_polygon(entry, base) if a.status != "invalid" or (base / entry.get("file", "_missing_")).exists() else (None, [], False)
    for pr in problems:
        a.cap("invalid", pr)
    if repaired:
        a.reasons.append("invalid geometry was repaired in memory (make_valid); the stored file is unchanged")
    return GeoRecord(entry["id"], entry.get("name", entry["id"]), kind, geom, entry.get("source", {}), entry.get("sha256", ""),
                     a.status, a.reasons, entry.get("district_id"), None, repaired)


class Registry:
    def __init__(self, base: Path, manifest: dict, district: GeoRecord | None, waterbodies: list):
        self.base, self.manifest, self.district, self.waterbodies = base, manifest, district, waterbodies

    @property
    def pilot(self) -> dict:
        return self.manifest["pilot"]

    @classmethod
    def load(cls, base: Path = DEFAULT_DIR) -> "Registry":
        base = Path(base)
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        pid = manifest["pilot"]["district_id"]
        entry = next((d for d in manifest.get("districts", []) if d["id"] == pid), None)
        district = _build(entry, "district", base) if entry else None
        wbs = []
        for e in manifest.get("waterbodies", []):
            r = _build(e, "waterbody", base)
            r.district_id = e.get("district_id", pid)
            _check_waterbody(r, district)
            wbs.append(r)
        return cls(base, manifest, district, wbs)

    def waterbody(self, wb_id: str) -> GeoRecord:
        for w in self.waterbodies:
            if w.id == wb_id:
                return w
        raise KeyError(f"unknown waterbody id {wb_id!r}")

    
    def blockers(self) -> list:
        out, p = [], self.pilot

        required = ("region", "district_id", "district_name", "seasonal_profile")
        missing = [key for key in required if not str(p.get(key, "")).strip()]

        if missing:
            out.append(
                f"Pilot configuration incomplete: missing {', '.join(missing)}."
            )

        if self.district is None:
            out.append(
                f"No boundary registered for pilot district "
                f"'{p.get('district_name', 'Unknown')}' "
                f"(id {p.get('district_id', 'Unknown')}). "
                "Register an authoritative boundary using "
                "`python -m cs.registry add-district`."
            )
        elif not self.district.verified:
            out.append(
                f"Pilot district boundary is '{self.district.status}': "
                + "; ".join(self.district.reasons)
            )

        if not self.waterbodies:
            out.append("No waterbody/AOI records registered.")
        else:
            matching = [
                w for w in self.waterbodies
                if w.district_id == p.get("district_id")
            ]

            if not matching:
                out.append(
                    "No waterbody records belong to the configured pilot district."
                )
            elif not any(w.verified for w in matching):
                out.append(
                    "No waterbody record is verified within the pilot district."
                )

            for w in self.waterbodies:
                if w.district_id != p.get("district_id"):
                    out.append(
                        f"Waterbody '{w.name}' references district "
                        f"'{w.district_id}', not the configured pilot district."
                    )

        return out


    def summary(self) -> dict:
        return {"pilot": self.pilot,
                "district": self.district.provenance() if self.district else None,
                "waterbodies": [w.provenance() for w in self.waterbodies], "blockers": self.blockers()}


def _check_waterbody(wb: GeoRecord, district: GeoRecord | None) -> None:
    if wb.geometry is None:
        return
    if district is None or district.geometry is None:
        wb.status = min((wb.status, "unverified"), key=RANK.get)
        wb.reasons.append("pilot district geometry unavailable; containment cannot be checked")
        return
    c = wb.geometry.centroid
    epsg = utm_epsg_for_lonlat(c.x, c.y)
    g = gpd.GeoSeries([wb.geometry, district.geometry], crs=4326).to_crs(epsg)
    wb.containment = float(g.iloc[0].intersection(g.iloc[1]).area / g.iloc[0].area) if g.iloc[0].area else 0.0
    if wb.containment < MIN_CONTAINMENT:
        wb.status = min((wb.status, "invalid"), key=RANK.get)
        wb.reasons.append(f"only {wb.containment:.0%} of the waterbody lies inside the pilot district (need >= {MIN_CONTAINMENT:.0%})")
    elif not district.verified:
        wb.status = min((wb.status, "unverified"), key=RANK.get)
        wb.reasons.append(f"pilot district is '{district.status}', so the waterbody cannot be verified")


def analysis_aoi(wb: GeoRecord, district: GeoRecord | None, buffer_m: float = 500.0) -> BaseGeometry:
    """Monitoring zone = waterbody + buffer (metres, computed in UTM), clipped to the pilot district when available.
    Returned in EPSG:4326. Encroachment is looked for at the margin, so the polygon (not a rectangle) is the AOI."""
    if wb.geometry is None:
        raise ValueError(f"waterbody {wb.id} has no usable geometry ({'; '.join(wb.reasons)})")
    c = wb.geometry.centroid
    epsg = utm_epsg_for_lonlat(c.x, c.y)
    buf = gpd.GeoSeries([wb.geometry], crs=4326).to_crs(epsg).buffer(buffer_m)
    if district is not None and district.geometry is not None:
        buf = buf.intersection(gpd.GeoSeries([district.geometry], crs=4326).to_crs(epsg).iloc[0])
    return buf.to_crs(4326).iloc[0]


def jurisdiction_text(reg: Registry) -> tuple[str, str]:
    d = reg.district
    if d is None:
        return "", "no district registered"
    return (f"{d.name} district ({reg.pilot['region']})",
            f"district boundary status: {d.status}; source: {d.source.get('publisher', '?')}")


# ---------------------------------------------------------------------------------------------- CLI
def _add(kind: str, a: argparse.Namespace) -> int:
    base = Path(a.registry)
    mpath = base / "manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    key = "districts" if kind == "district" else "waterbodies"
    eid = a.id or (manifest["pilot"]["district_id"] if kind == "district" else None)
    if not eid:
        sys.exit("--id is required")
    if any(e["id"] == eid for e in manifest[key]):
        sys.exit(f"{kind} id '{eid}' already registered")
    src = Path(a.file)
    digest = sha256_file(src)
    dest_rel = f"sources/{digest[:12]}_{src.name}"
    (base / "sources").mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, base / dest_rel)
    entry = {"id": eid, "name": a.name, "file": dest_rel, "sha256": digest,
             "source": {"publisher": a.publisher, "dataset": a.dataset, "url": a.url, "licence": a.licence,
                        "retrieved_utc": a.retrieved, "vintage": a.vintage or ""}}
    if a.crs:
        entry["crs"] = a.crs
    if a.field:
        entry["feature_filter"] = {"field": a.field, "value": a.value}
    if kind == "waterbody":
        entry["district_id"] = manifest["pilot"]["district_id"]
    manifest[key].append(entry)
    tmp = mpath.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(mpath)
    reg = Registry.load(base)
    rec = reg.district if kind == "district" else reg.waterbody(eid)
    print(json.dumps(rec.provenance(), indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cs.registry", description=__doc__)
    ap.add_argument("--registry", default=str(DEFAULT_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("add-district", "add-waterbody"):
        p = sub.add_parser(name)
        p.add_argument("--file", required=True)
        p.add_argument("--name", required=True)
        p.add_argument("--id")
        p.add_argument("--field", help="attribute column used to select the feature(s) from a multi-feature file")
        p.add_argument("--value", help="value of --field identifying this feature")
        p.add_argument("--crs", help="declare the CRS only if the file lacks one")
        p.add_argument("--publisher", required=True)
        p.add_argument("--dataset", required=True)
        p.add_argument("--url", required=True, help="URL the file was obtained from")
        p.add_argument("--licence", required=True)
        p.add_argument("--retrieved", required=True, help="ISO date the file was retrieved")
        p.add_argument("--vintage")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(Registry.load(Path(a.registry)).summary(), indent=2, default=str))
        return 0
    if bool(a.field) != bool(a.value):
        sys.exit("--field and --value must be given together")
    return _add("district" if a.cmd == "add-district" else "waterbody", a)


if __name__ == "__main__":
    raise SystemExit(main())
