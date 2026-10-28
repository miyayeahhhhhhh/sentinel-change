import json
import rasterio
from cs.db import CaseStore
from cs.report import build_pdf
from cs.workflow import execute_encroachment, execute_flood


def test_end_to_end_both_pipelines(settings, tmp_path):
    r1, c1 = execute_flood(settings, "flood_event")
    r2, c2 = execute_encroachment(settings, "persistent_loss")
    assert c1.startswith("FLD-") and c2.startswith("ENC-")
    store = CaseStore(settings.db_path)
    assert [c["case_id"] for c in store.list_cases("flood")] == [c1]
    assert [c["case_id"] for c in store.list_cases("encroachment")] == [c2]
    ex = json.loads(store.get_case(c1)["exports"])
    with rasterio.open(ex["change_mask_tif"]) as src:
        assert src.crs.to_epsg() == 32643 and src.nodata == 255
        arr = src.read(1)
        assert (arr == 255).sum() * 0.01 == r1.evidence["unobserved_ha"]   # unobserved pixels are nodata, never 0
    gj = json.loads(open(ex["polygons_geojson"]).read())
    assert gj["type"] == "FeatureCollection" and gj["features"]
    rec = json.loads(open(ex["evidence_json"]).read())
    assert rec["score_label"] and "Not a probability" in rec["score_label"]
    pdf = build_pdf(store, c2, tmp_path / "c.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"


def test_not_assessed_is_recorded_not_dropped(settings):
    r, cid = execute_flood(settings, "insufficient")
    c = CaseStore(settings.db_path).get_case(cid)
    assert c["state"] == "not_assessed" and c["score"] is None


def test_feedback_does_not_rewrite_history_and_is_audited(settings):
    _, cid = execute_encroachment(settings, "persistent_loss")
    st = CaseStore(settings.db_path)
    before = st.get_case(cid)["evidence"]
    st.set_status(cid, "under_review", "alice")
    st.add_decision(cid, "alice", "natural_seasonal", "looks seasonal")
    after = st.get_case(cid)
    assert after["evidence"] == before and after["state"] == "high_priority_review" and after["status"] == "under_review"
    actions = [a["action"] for a in st.audit(cid)]
    assert actions == ["case_created", "status_changed", "reviewer_decision"]
    assert st.decisions(cid)[0]["decision"] == "natural_seasonal"
