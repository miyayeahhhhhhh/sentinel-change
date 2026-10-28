# Architecture note
| Module | Responsibility |
|---|---|
| `app.py` | Streamlit UI only (workspaces, queues, methods page) |
| `cs/config.py` | Settings + separate `FloodParams` / `EncroachParams` |
| `cs/models.py` | `State`, `Result`, score-label rules (score only for flagged states), `failed_result` |
| `cs/geo.py` | Grid validation (projected metres only), mask cleanup, vectorisation, GeoTIFF/GeoJSON IO |
| `cs/imagery.py`, `cs/sample_data.py` | Provider adapters; working synthetic provider; live provider = scaffold |
| `cs/flood.py` | S1 pipeline + flood-only score (F1-F4) |
| `cs/encroach.py` | S2 pipeline + persistence rule + encroachment-only score (E1-E4) |
| `cs/exports.py`, `cs/report.py`, `cs/mapview.py` | Exports with validation, ReportLab PDF, Folium layers |
| `cs/db.py`, `cs/priority.py`, `cs/workflow.py` | SQLite cases/audit/decisions, separate queues, orchestration |

Separation is enforced by tests (no cross-imports, distinct weights/scorers/params). Unfinished: live imagery, DEM/HAND/shadow masks,
OSM exposure, jurisdiction join, S1/S2 corroboration, water-level-proxy normalisation, multi-AOI/water-body registry, validation tables.
