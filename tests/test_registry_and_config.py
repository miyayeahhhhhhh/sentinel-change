import json
import shutil
import pytest
import geopandas as gpd
from shapely.geometry import box

from cs import registry as R
from cs.config import (SEASONAL_PROFILES, Settings, UnknownStudyArea, encroach_params_for, utc)
from cs.evidence import Unavailable, DynamicWorldSource, EvidenceRequest, JRCSurfaceWaterSource, Sentinel1Source, evidence_sources, NotConfigured, qc_db
from cs.study_areas import make_grid
from cs.workflow import execute_encroachment_for_waterbody
import numpy as np

SRC_OFFICIAL = dict(publisher="TEST PUBLISHER", dataset="TEST DATASET", url="https://data.example.gov.in/x", licence="OGDL-India", retrieved="2026-10-01")


@pytest.fixture
def reg_dir(tmp_path):
    d = tmp_path / "reg"; d.mkdir()
    from tests.conftest import write_empty_manifest
    write_empty_manifest(d)
    return d


def _write_test_file(tmp_path, geoms, names, name="f.geojson"):
    p = tmp_path / name
    gpd.GeoDataFrame({"dname": names}, geometry=geoms, crs=4326).to_file(p, driver="GeoJSON")   # TEST geometry, not real boundaries
    return p


def _add(reg_dir, kind, file, name, id_=None, field=None, value=None, **over):
    s = {**SRC_OFFICIAL, **over}
    argv = ["--registry", str(reg_dir), f"add-{kind}", "--file", str(file), "--name", name, "--publisher", s["publisher"],
            "--dataset", s["dataset"], "--url", s["url"], "--licence", s["licence"], "--retrieved", s["retrieved"]]
    if id_: argv += ["--id", id_]
    if field: argv += ["--field", field, "--value", value]
    return R.main(argv)


def test_shipped_registry_records_carry_provenance_and_computed_status():
    """The registry shipped with the project may contain real records, but never an unsourced or unchecked one."""
    reg = R.Registry.load()
    for rec in ([reg.district] if reg.district else []) + list(reg.waterbodies):
        assert rec.source.get("publisher") and rec.source.get("url") and rec.sha256, rec.id
        if rec.status == "verified":
            assert not rec.reasons and rec.geometry is not None
    if reg.district is None:
        assert any("No boundary registered" in b for b in reg.blockers())


def test_empty_registry_blocks_waterbody_run(settings, reg_dir):
    with pytest.raises(KeyError):
        execute_encroachment_for_waterbody(settings, "nope", registry=R.Registry.load(reg_dir))


def test_official_host_is_verified_and_third_party_is_provisional(tmp_path, reg_dir, capsys):
    f = _write_test_file(tmp_path, [box(78.3, 17.3, 78.6, 17.5), box(78.0, 17.0, 78.1, 17.1)], ["Hyderabad", "Other"])
    _add(reg_dir, "district", f, "Hyderabad", field="dname", value="Hyderabad")
    reg = R.Registry.load(reg_dir)
    assert reg.district.status == "verified" and reg.district.geometry.bounds[0] == pytest.approx(78.3)
    reg2dir = reg_dir.parent / "reg2"; shutil.copytree(reg_dir, reg2dir)
    m = json.loads((reg2dir / "manifest.json").read_text()); m["districts"][0]["source"]["url"] = "https://github.com/someone/repo"
    (reg2dir / "manifest.json").write_text(json.dumps(m))
    d2 = R.Registry.load(reg2dir).district
    assert d2.status == "provisional" and any("not an official government domain" in r for r in d2.reasons)


def test_checksum_tamper_and_missing_provenance(tmp_path, reg_dir):
    f = _write_test_file(tmp_path, [box(78.3, 17.3, 78.6, 17.5)], ["Hyderabad"])
    _add(reg_dir, "district", f, "Hyderabad")
    m = json.loads((reg_dir / "manifest.json").read_text())
    stored = reg_dir / m["districts"][0]["file"]
    stored.write_text(stored.read_text().replace("78.3", "78.31"))
    assert R.Registry.load(reg_dir).district.status == "invalid"
    m["districts"][0]["source"].pop("licence"); (reg_dir / "manifest.json").write_text(json.dumps(m))
    assert R.Registry.load(reg_dir).district.status in ("invalid", "unverified")


def test_implausible_coordinates_rejected(tmp_path, reg_dir):
    f = _write_test_file(tmp_path, [box(10, 10, 11, 11)], ["Hyderabad"])
    _add(reg_dir, "district", f, "Hyderabad")
    d = R.Registry.load(reg_dir).district
    assert d.status == "invalid" and d.geometry is None


def test_waterbody_containment_and_unverified_district(tmp_path, reg_dir):
    dist = _write_test_file(tmp_path, [box(78.3, 17.3, 78.6, 17.5)], ["Hyderabad"], "d.geojson")
    inside = _write_test_file(tmp_path, [box(78.40, 17.40, 78.42, 17.42)], ["in"], "in.geojson")
    outside = _write_test_file(tmp_path, [box(78.7, 17.4, 78.72, 17.42)], ["out"], "out.geojson")
    _add(reg_dir, "waterbody", inside, "In lake", id_="in")                   # no district yet
    assert R.Registry.load(reg_dir).waterbody("in").status == "unverified"
    _add(reg_dir, "district", dist, "Hyderabad")
    _add(reg_dir, "waterbody", outside, "Out lake", id_="out")
    reg = R.Registry.load(reg_dir)
    assert reg.waterbody("in").verified and reg.waterbody("in").containment == pytest.approx(1.0)
    assert reg.waterbody("out").status == "invalid"
    aoi = R.analysis_aoi(reg.waterbody("in"), reg.district, 500.0)
    assert aoi.contains(reg.waterbody("in").geometry) and aoi.area > reg.waterbody("in").geometry.area
    assert reg.district.geometry.contains(aoi)                                 # clipped to the district


def test_registry_run_refuses_unverified_and_sample_mode(settings, tmp_path, reg_dir):
    dist = _write_test_file(tmp_path, [box(78.3, 17.3, 78.6, 17.5)], ["Hyderabad"], "d.geojson")
    wb = _write_test_file(tmp_path, [box(78.40, 17.40, 78.42, 17.42)], ["x"], "w.geojson")
    _add(reg_dir, "district", dist, "Hyderabad")
    _add(reg_dir, "waterbody", wb, "Third party lake", id_="tp", url="https://github.com/x/y")
    reg = R.Registry.load(reg_dir)
    with pytest.raises(NotConfigured, match="unverified|provisional"):
        execute_encroachment_for_waterbody(settings, "tp", registry=reg)
    with pytest.raises(NotConfigured, match="CS_MODE=live"):                  # sample mode never substitutes data
        execute_encroachment_for_waterbody(settings, "tp", registry=reg, allow_unverified=True)


def test_seasonal_params_follow_declared_profile_not_ui_defaults():
    base = encroach_params_for("pallikaranai_marsh")
    assert base.wet_months == (10, 11, 12) and base.recent_start == utc(2024, 1, 1)
    layered = encroach_params_for("pallikaranai_marsh", base=__import__("dataclasses").replace(base, persist=0.9))
    assert layered.wet_months == (10, 11, 12) and layered.persist == 0.9
    assert encroach_params_for(profile="sw_monsoon").wet_months == (6, 7, 8, 9, 10)
    with pytest.raises(ValueError):
        encroach_params_for(profile="sw_monsoon", as_of=utc(2026, 10, 31), recent_months=6)
    assert all(not p.validated for p in SEASONAL_PROFILES.values())          # honest: nothing is claimed validated


def test_unknown_grid_and_bad_mode_fail_loudly():
    with pytest.raises(UnknownStudyArea):
        make_grid("solapur_bhima")
    with pytest.raises(ValueError):
        Settings(mode="sample # comment")


def test_evidence_sources_never_fabricate(settings):
    srcs = evidence_sources()
    assert set(srcs) == {"sentinel1", "dynamic_world", "jrc_gsw"}
    assert all(s.describe()["tested_against_remote"] is False for s in srcs.values())
    g = make_grid()
    req = EvidenceRequest(g, np.ones((g.height, g.width), bool), utc(2022, 1, 1), utc(2025, 12, 31), "x", utc(2025, 1, 1))
    for s in (DynamicWorldSource(), JRCSurfaceWaterSource(), Sentinel1Source()):
        out = s.fetch(req)                                       # no client / transport / cache -> explicit Unavailable, never data
        assert isinstance(out, Unavailable) and out.reason
    assert np.isnan(qc_db(np.array([[-50.0, -12.0, 9.0]]))[0, [0, 2]]).all()
