# Architecture — Base Application

| Module | Responsibility |
|---|---|
| `app.py` | Streamlit dashboard, registry, analysis workspace, case management and readiness UI |
| `cs/config.py` | Environment settings and named seasonal parameter profiles |
| `cs/study_areas.py` | Fixed validation/test grids; synthetic fixtures are explicitly labelled |
| `cs/registry.py` | Provenance-tracked district/waterbody registry, checksums, geometry validation and AOI creation |
| `cs/geo.py` | CRS-aware grids, reprojection, polygon masks and raster I/O |
| `cs/imagery.py` | Sample fixtures and Sentinel-2 live provider |
| `cs/evidence/` | Evidence-source contracts and optional adapters; deferred sources are not activated by the base UI |
| `cs/encroach.py` | Sentinel-2 MNDWI persistence pipeline, seasonal safeguards and review-priority score |
| `cs/workflow.py` | Analysis execution, export and immutable case creation |
| `cs/mapview.py` | Folium maps and evidence layers |
| `cs/db.py` | SQLite case store, notes, decisions and audit log |
| `cs/priority.py` | Review queue ordering |
| `cs/exports.py`, `cs/report.py` | Geospatial artefacts and PDF case reports |
| `cs/integrations/` | Future integration adapters; no live integration is claimed by this base release |

## Data ownership

SQLite is the authoritative case store. Analysis evidence is captured with the case. Reviewer decisions, notes and audit events are separate records; reviewer actions do not rewrite the original sensor evidence.

## Integration boundary

Sentinel-1 acquisition, Dynamic World, JRC GSW, Neo4j, email, Sarvam AI and authentication/authorization should be integrated behind explicit service boundaries in a later phase. An adapter being present in the repository does not mean the service is configured, reachable, validated or contributing to results.

## Result-state contract

- `no_change_detected`: sufficient usable evidence was analysed and no indicator crossed the configured threshold.
- `not_assessed`: data coverage or source availability did not support an assessment.
- `analysis_failed`: processing failed; no conclusion can be drawn.
- `potential_change_detected` / `high_priority_review`: heuristic screening indicators requiring human review.

Never map missing/unobserved pixels to no change.
