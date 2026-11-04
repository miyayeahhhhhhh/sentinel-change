from cs.db import CaseStore
from cs.models import Pipeline, Result, State
from cs.geo import Grid
from cs.priority import queue_table
from rasterio.crs import CRS
from rasterio.transform import from_origin
from cs.neo4j_dashboard_extension import read_graph


def _stored_case(case_id, pipeline, state):
    grid = Grid(from_origin(500000, 2000000, 10, 10), CRS.from_epsg(32644), 2, 2)
    result = Result(
        pipeline=Pipeline(pipeline), state=State(state), summary=f"Stored {case_id}",
        evidence={"case_detail": case_id, "period": {"start": "2024-01-01", "end": "2024-02-01"}},
        limitations=["recorded limitation"], params={"recorded": True}, grid=grid, arrays={}, primary_layer="loss",
        score=None, score_label="Not assessed", synthetic=False,
    )
    return result


def test_all_stored_cases_are_listed_and_selectable_without_pipeline_filter(tmp_path):
    store = CaseStore(tmp_path / "cases.db")
    for cid, state in (("ENC-OLD", "no_change_detected"), ("ENC-FAILED", "analysis_failed")):
        store.create_case(cid, _stored_case(cid, "encroachment", state), {})
    with store._c() as conn:
        conn.execute("UPDATE cases SET pipeline='legacy_pipeline' WHERE case_id='ENC-FAILED'")

    cases = store.list_cases()
    queue = queue_table(cases)
    assert set(queue["case_id"]) == {"ENC-OLD", "ENC-FAILED"}
    for cid in queue["case_id"]:
        case = store.get_case(cid)
        assert case["case_id"] == cid
        assert cid in case["evidence"]


class _Result:
    def data(self):
        return [
            {"source_id": "node-1", "labels": ["Case"], "properties": {"case_id": "ENC-1"},
             "rel_id": "rel-1", "rel_type": "HAS_ANALYSIS", "rel_properties": {"source": "stored"},
             "target_id": "node-2", "target_labels": ["AnalysisRun"], "target_properties": {"run": 1}},
            {"source_id": "isolated", "labels": ["Archive"], "properties": {"name": "stored isolated node"},
             "rel_id": None, "rel_type": None, "rel_properties": None,
             "target_id": None, "target_labels": None, "target_properties": None},
        ]


class _Session:
    def __init__(self):
        self.query = None

    def run(self, query):
        self.query = query
        return _Result()


def test_graph_reader_uses_returned_nodes_relationships_and_keeps_isolated_nodes():
    session = _Session()
    nodes, edges = read_graph(session)
    assert "OPTIONAL MATCH (n)-[r]->(m)" in session.query
    assert set(nodes) == {"node-1", "node-2", "isolated"}
    assert nodes["node-1"]["properties"]["case_id"] == "ENC-1"
    assert edges == {"rel-1": {"id": "rel-1", "from": "node-1", "to": "node-2",
                                "type": "HAS_ANALYSIS", "properties": {"source": "stored"}}}
