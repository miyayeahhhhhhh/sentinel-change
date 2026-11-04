"""Drop-in, read-only Neo4j panel for Change Sentinel's Streamlit app."""
from __future__ import annotations
import os
import json
import streamlit as st
import streamlit.components.v1 as components


def read_graph(session) -> tuple[dict, dict]:
    """Read every stored node (including isolated nodes) and outgoing relationship."""
    records = session.run(
        "MATCH (n) OPTIONAL MATCH (n)-[r]->(m) "
        "RETURN elementId(n) AS source_id, labels(n) AS labels, properties(n) AS properties, "
        "elementId(r) AS rel_id, type(r) AS rel_type, properties(r) AS rel_properties, "
        "elementId(m) AS target_id, labels(m) AS target_labels, properties(m) AS target_properties"
    ).data()
    nodes, edges = {}, {}
    for row in records:
        source_id = row["source_id"]
        nodes[source_id] = {"id": source_id, "labels": row["labels"], "properties": row["properties"]}
        if row["target_id"]:
            target_id = row["target_id"]
            nodes[target_id] = {"id": target_id, "labels": row["target_labels"], "properties": row["target_properties"]}
        if row["rel_id"]:
            edges[row["rel_id"]] = {"id": row["rel_id"], "from": source_id, "to": row["target_id"],
                                     "type": row["rel_type"], "properties": row["rel_properties"]}
    return nodes, edges


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def render_neo4j_panel() -> None:
    """Render actual Neo4j nodes and relationships without modifying the database."""
    st.subheader("Neo4j Knowledge Graph")
    uri = _setting("CS_NEO4J_URI")
    user = _setting("CS_NEO4J_USER")
    password = _setting("CS_NEO4J_PASSWORD")
    if not uri or not user or not password:
        st.info("Neo4j is not configured. Add CS_NEO4J_URI, CS_NEO4J_USER and CS_NEO4J_PASSWORD to your deployment secrets, then restart the app.")
        st.code("CS_NEO4J_URI=neo4j+s://<your-instance>.databases.neo4j.io\nCS_NEO4J_USER=neo4j\nCS_NEO4J_PASSWORD=<your-password>", language="text")
        st.caption("Keep credentials in Streamlit Secrets or environment variables. Never commit them to GitHub.")
        return

    try:
        from neo4j import GraphDatabase
    except ImportError:
        st.error("Neo4j Python driver is missing. Install it with: pip install neo4j")
        return

    if not st.button("Connect / refresh graph", key="cs_neo4j_refresh"):
        st.caption("Credentials detected. Select Connect / refresh graph to check the database.")
        return

    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password), connection_timeout=8)
        driver.verify_connectivity()
        with driver.session() as session:
            nodes, edges = read_graph(session)
        st.success("Connected to Neo4j successfully")
        c1, c2 = st.columns(2)
        c1.metric("Nodes", f"{len(nodes):,}")
        c2.metric("Relationships", f"{len(edges):,}")
        if not nodes:
            st.info("The connected Neo4j database contains no nodes.")
        else:
            graph_nodes = [{"id": n["id"], "label": ":".join(n["labels"]) or "Node",
                            "title": json.dumps({"labels": n["labels"], "properties": n["properties"]}, default=str),
                            "shape": "dot"} for n in nodes.values()]
            graph_edges = [{"id": e["id"], "from": e["from"], "to": e["to"],
                            "label": e["type"], "title": json.dumps(e["properties"], default=str), "arrows": "to"}
                           for e in edges.values()]
            payload = json.dumps({"nodes": graph_nodes, "edges": graph_edges}, default=str).replace("</", "<\\/")
            components.html(f"""<div id="graph" style="height:620px;width:100%;border:1px solid #888;border-radius:4px"></div>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<script>const data={payload}; const container=document.getElementById('graph');
const network=new vis.Network(container,{{nodes:new vis.DataSet(data.nodes),edges:new vis.DataSet(data.edges)}},
{{interaction:{{hover:true,navigationButtons:true,keyboard:true}},physics:{{stabilization:true}},
nodes:{{font:{{color:'#111'}}}},edges:{{font:{{color:'#111',align:'middle'}}}}}});</script>""", height=640, scrolling=True)
        st.caption("Read-only view of nodes and relationships returned by the connected Neo4j database. Hover for stored properties; drag to explore.")
    except Exception as exc:
        st.error(f"Neo4j connection failed: {type(exc).__name__}: {exc}")
        st.caption("Check the Aura/Neo4j URI, username, password, network access and database status.")
    finally:
        if driver is not None:
            driver.close()
