# Change Sentinel — Base Build Status (2026-10-31)

## Release objective

Deliver a stable base application first. External data providers, graph storage, email, translation and production authentication are separate future integration milestones.

## Base application features

| Area | Current status |
|---|---|
| Dashboard | Added: case metrics, AOI readiness, source readiness and recent-case summary |
| Pilot registry | Implemented: provenance, checksum, geometry and containment checks |
| Analysis workspace | Implemented: Sentinel-2 MNDWI persistence, seasonal safeguards, coverage-aware states and map layers |
| Case management | Implemented: queue, status updates, reviewer notes, formal decisions and audit history |
| Exports | Implemented: PDF, GeoTIFF, GeoJSON and JSON |
| Methods/readiness | Implemented: method explanation, limitations and deferred integration status |
| Evidence integrity | Implemented in pipeline: missing/insufficient evidence is not a clean result |
| Authentication | Deferred: operator label is audit attribution only, not authentication or authorization |

## Deferred integrations

| Integration | Base-build treatment |
|---|---|
| Sentinel-1 | Not connected; no acquisition |
| Dynamic World | Not activated in UI; not included in scoring |
| JRC Global Surface Water | Not activated in UI; historical data must not be treated as current observation |
| Neo4j | Not connected; SQLite remains authoritative |
| Email | No outbound messages sent |
| Sarvam AI | No translation requests sent |
| Authentication / roles | Admin, Analyst and Reviewer access control not yet implemented |

Optional adapter modules may remain in the source tree, but they must not be described as live or externally verified.

## Geography and operational blockers

- Hyderabad district boundary is not registered from a verified authoritative source.
- No verified Hyderabad waterbody/AOI records are registered.
- The legacy Pallikaranai Chennai cache is not Hyderabad evidence.
- Registry AOIs require live Sentinel-2 imagery; sample fixtures cannot substitute for real AOIs.
- Live external acquisition was not validated during this build.
- Seasonal profiles are assumed defaults, not field-validated.
- Threshold calibration, DEM/HAND filtering, OSM exposure joins and field validation remain outstanding.

## Test baseline

The supplied project test suite was run before UI refinements: **70 passed, 1 skipped** (excluding the Streamlit smoke test). Re-run the full test suite after installing `requirements.txt`. External service connectivity has not been tested and is outside this base-build scope.

## Safety / interpretation

A potential-change indicator is not a legal finding. Scores are heuristic review-priority indicators, not probabilities. Unavailable imagery, missing credentials, low coverage and processing errors must remain explicit unavailable/unassessed/failed states. Never manufacture a detection or report a false clean result.
