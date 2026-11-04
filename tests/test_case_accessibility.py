from cs.db import CaseStore
from cs.geo import Grid
from cs.models import Pipeline, Result, State
from cs.priority import filter_queue_by_case_id, queue_table
from rasterio.crs import CRS
from rasterio.transform import from_origin


def _create_case(store, case_id, state=State.NO_CHANGE):
    result = Result(
        pipeline=Pipeline.ENCROACH,
        state=state,
        summary=f"Stored record {case_id}",
        evidence={"case_id_detail": case_id, "period": {"start": "2024-01-01", "end": "2024-02-01"}},
        limitations=["Recorded limitation"],
        params={"recorded_parameter": True},
        grid=Grid(from_origin(500000, 2000000, 10, 10), CRS.from_epsg(32644), 2, 2),
        arrays={},
        primary_layer="loss",
        score=70 if state == State.HIGH else None,
        synthetic=False,
    )
    exports = {name: f"not-present/{case_id}.{suffix}" for name, suffix in (
        ("preview_png", "png"), ("change_mask_tif", "tif"), ("coverage_tif", "tif"),
        ("polygons_geojson", "geojson"), ("evidence_json", "json"),
    )}
    store.create_case(case_id, result, exports)


def test_complete_case_queue_has_no_display_limit_and_searches_case_ids(tmp_path):
    store = CaseStore(tmp_path / "cases.db")
    case_ids = [f"ENC-{i:04d}" for i in range(1, 18)]
    for case_id in case_ids:
        _create_case(store, case_id)

    complete_queue = queue_table(store.list_cases())
    assert set(complete_queue["case_id"]) == set(case_ids)
    assert len(complete_queue) == len(case_ids)  # No pagination is needed; no arbitrary page cap drops records.
    assert filter_queue_by_case_id(complete_queue, " enc-0010 ")["case_id"].tolist() == ["ENC-0010"]
    assert filter_queue_by_case_id(complete_queue, "missing").empty
    assert len(filter_queue_by_case_id(complete_queue, "")) == len(case_ids)


def test_state_and_review_status_filters_still_allow_opening_full_case(tmp_path):
    store = CaseStore(tmp_path / "cases.db")
    _create_case(store, "ENC-0001", State.NO_CHANGE)
    _create_case(store, "ENC-0002", State.HIGH)
    store.set_status("ENC-0001", "reviewed", "reviewer")

    filtered = store.list_cases(states=[State.NO_CHANGE.value], status="reviewed")
    assert [row["case_id"] for row in filtered] == ["ENC-0001"]

    opened = store.get_case("ENC-0001")
    assert opened["case_id"] == "ENC-0001"
    assert '"case_id_detail": "ENC-0001"' in opened["evidence"]
    assert store.audit("ENC-0001")[-1]["action"] == "status_changed"


def test_case_management_search_selects_and_opens_matching_stored_case(monkeypatch, tmp_path):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    db_path = tmp_path / "case-management.db"
    monkeypatch.setenv("CS_DB_PATH", str(db_path))
    monkeypatch.setenv("CS_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    for key in ("CS_NEO4J_URI", "CS_NEO4J_USER", "CS_NEO4J_PASSWORD", "CS_SMTP_HOST", "CS_SMTP_FROM"):
        monkeypatch.delenv(key, raising=False)
    store = CaseStore(db_path)
    _create_case(store, "ENC-0015")
    _create_case(store, "ENC-0016")
    _create_case(store, "ENC-0017")

    project_root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(project_root / "app.py"), default_timeout=120).run()
    app.sidebar.radio(key="page_nav").set_value("Case management").run()
    app.text_input(key="case_id_search").set_value("0016").run()

    case_selector = next(widget for widget in app.selectbox if widget.label == "Select Case to Inspect")
    assert case_selector.options == ["ENC-0016"]
    assert any("Case ENC-0016" in item.value for item in app.markdown)
    assert not app.exception
