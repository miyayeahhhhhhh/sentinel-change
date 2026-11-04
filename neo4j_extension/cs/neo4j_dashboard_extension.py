"""Drop-in, read-only Neo4j panel for Change Sentinel's Streamlit app."""
from __future__ import annotations
import os
import streamlit as st


def _setting(name: str, default: str = "") -> str:
    value = os.getenv(name, "")
    if value:
        return value
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def render_neo4j_panel() -> None:
    """Render a safe Neo4j connection/graph overview. No writes are performed."""
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
            record = session.run("MATCH (n) RETURN count(n) AS nodes").single()
            rel = session.run("MATCH ()-[r]->() RETURN count(r) AS relationships").single()
            labels = session.run("CALL db.labels() YIELD label RETURN label ORDER BY label").value()
            rel_types = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType").value()
        st.success("Connected to Neo4j successfully")
        c1, c2 = st.columns(2)
        c1.metric("Nodes", f"{record['nodes']:,}")
        c2.metric("Relationships", f"{rel['relationships']:,}")
        st.markdown("**Node labels**")
        st.write(", ".join(labels) if labels else "No labels found yet.")
        st.markdown("**Relationship types**")
        st.write(", ".join(rel_types) if rel_types else "No relationships found yet.")
        st.caption("This panel is read-only. SQLite remains the system of record; use the existing Neo4j sync workflow to mirror case data.")
    except Exception as exc:
        st.error(f"Neo4j connection failed: {type(exc).__name__}: {exc}")
        st.caption("Check the Aura/Neo4j URI, username, password, network access and database status.")
    finally:
        if driver is not None:
            driver.close()
