from __future__ import annotations
import os, json


def sync_case(case: dict) -> tuple[bool, str]:
    uri, user, password = (os.getenv(k, "").strip() for k in ("CS_NEO4J_URI", "CS_NEO4J_USER", "CS_NEO4J_PASSWORD"))
    if not all((uri, user, password)): return False, "Neo4j not configured."
    try: from neo4j import GraphDatabase
    except ImportError: return False, "Install neo4j Python driver to enable graph sync."
    try:
        evidence = json.loads(case.get("evidence") or "{}")
    except (TypeError, ValueError): evidence = {}
    aoi = evidence.get("aoi") or {}
    sources = evidence.get("supplementary_evidence") or {}
    query = """MERGE (c:Case {case_id:$case_id}) SET c.pipeline=$pipeline, c.state=$state,
      c.status=$status, c.summary=$summary, c.score=$score, c.created_at=$created_at
      MERGE (r:AnalysisRun {case_id:$case_id}) SET r.period_start=$period_start, r.period_end=$period_end
      MERGE (c)-[:HAS_ANALYSIS]->(r)
      WITH c,r
      FOREACH (_ IN CASE WHEN $aoi_id IS NULL OR $aoi_id='' THEN [] ELSE [1] END |
        MERGE (w:Waterbody {aoi_id:$aoi_id}) SET w.name=$aoi_name, w.verification_status=$aoi_status
        MERGE (c)-[:FOR_AOI]->(w))
      WITH c,r
      UNWIND $sources AS src
      MERGE (e:EvidenceSource {key:src.key}) SET e.status=src.status, e.dataset=src.dataset
      MERGE (r)-[:HAS_EVIDENCE_SOURCE]->(e)"""
    params = {k: case.get(k) for k in ("case_id","pipeline","state","status","summary","score","created_at","period_start","period_end")}
    params.update({"aoi_id":aoi.get("id"), "aoi_name":aoi.get("name"), "aoi_status":aoi.get("status"),
                   "sources":[{"key":k,"status":v.get("status"),"dataset":(v.get("provenance") or {}).get("dataset",k)}
                              for k,v in sources.items()]})
    try:
        with GraphDatabase.driver(uri, auth=(user,password)) as driver:
            driver.verify_connectivity()
            with driver.session() as session: session.run(query, **params).consume()
        return True, "Case graph synchronized."
    except Exception as exc: return False, f"Neo4j sync failed: {type(exc).__name__}: {exc}"
