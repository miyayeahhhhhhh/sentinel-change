# Changelog

## Extension M3 (2026-11-03)
**P0 - correctness and safety**
- **Sentinel-2 acquisition** (`cs/imagery.py`): root cause of the single-scene run confirmed - one failing item aborted the fetch after the first scene was cached and any non-empty cache was then treated as complete. Now: per-item try/except with recorded errors; STAC pagination (`next` links); cloud filtering client-side so it is countable; atomic cache writes; `manifest.json` per cache dir (query, grid stamp, found / cloud-filtered / candidates / cached / failed, errors); a cache is complete only if its manifest matches the request and has no failures (otherwise STAC is queried again; already-cached items are not re-read); `refresh=True` / "Refresh imagery" option. Minimum-observation thresholds unchanged. Counts are stored in the case (`evidence.acquisition`) and shown in the workspace.
- **E-mail safety**: no pre-filled recipient; `guarded_send` blocks synthetic, legacy, unassessed, no-change and never-reviewed cases, needs a typed address and an explicit confirmation, audits every attempt (`report_email_sent/_failed/_blocked`); automatic alerts after a run follow the same rules (`email_alert_skipped`); native-language text is labelled machine-translated.
- **Scope** (`cs/scope.py`): Hyderabad pilot (real) / Synthetic / Legacy Chennai filter on dashboard and queue, default pilot; dashboard counts only count real, genuinely assessed pilot cases; badges on synthetic/legacy rows.
- **Review status**: UI-layer mapping (`unassessed`, `analysis failed`, `no change detected - screening only`); stored rows and audit history untouched.
- **Readiness generated, not hard-coded**: new `source_runs` table (created automatically, `CREATE TABLE IF NOT EXISTS`) written after every real attempt per source; Methods/Dashboard/Registry readiness tables come from registry status + adapter status + run log. `tested_against_remote` = a recorded successful run that was not a stand-in.
**P1** - map layer control collapsed; waterbody boundary on by default (orange); district outline dashed; no whole-AOI "data gap" shading for not-assessed cases (banner instead); empty groups explained; `use_container_width` -> `width="stretch"`; deleted `cs/flood.py` and the obsolete `cs/evidence/earth_engine.py`.
**P2** - Sentinel-1 RTC, Dynamic World and JRC GSW adapters + `cs/fusion.py` ported from Milestone 2 onto this base (verdicts supports/neutral/contradicts/insufficient/unavailable; contradiction caps score at 50 and blocks HIGH; registry runs without independent support capped at 50; no new numeric weights from these sources in the registry path). Dynamic World: tiled downloads for large AOIs and a result cache keyed on grid + window + bands. `scripts/diagnose_live.py` verifies the real-service assumptions locally and writes `diagnostics.json`.
**Not done / not tested**: no adapter has been run against a live service (sandbox had no network); JRC Earth Engine fallback and the JRC monthly-history seasonality check were not implemented.
**Tests**: base tests that assumed an empty shipped registry now use a hermetic empty registry; +20 focused tests (`tests/test_m3_acquisition_and_safety.py`) with stand-ins.

## Milestone 1 (2026-10-31)
**Removed from the product:** flood and drought workflows (`cs/flood.py`, `FloodParams`, `DroughtParams`, flood fixtures/UI/queue/tests), unused `neo4j`
dependency and settings, unused CDSE settings. Reusable SAR QC (`qc_db`) moved to `cs/evidence/sentinel1.py`.
**Added:** provenance registry + CLI (`cs/registry.py`); evidence-source package; CRS/UTM/grid-from-polygon/polygon-mask/reprojection/warped-read/Web-Mercator
overlay functions (`cs/geo.py`); polygon AOI analysis path (`execute_encroachment_for_waterbody`); grouped, independently toggleable map layers;
Pilot registry page; AOI + evidence-source sections in PDF/evidence JSON; `aoi.geojson` export; 28 new tests.
**Fixed (bugs found in the audit):**
- UI always passed default `EncroachParams` into the run, silently overriding area-specific seasonality (NE-monsoon wet months never applied to Pallikaranai via the UI). Controls now layer on top of the area's parameters.
- `encroach_params_for` hard-coded one place name and silently returned SW-monsoon defaults for anything else; `make_grid` silently fell back to a Solapur grid for unknown names. Both now raise; seasonality comes from a named profile.
- Map overlays were placed by lon/lat bbox of a UTM raster (misaligned) and the AOI drawn as a bbox. Rasters are now warped to EPSG:3857, clipped to the polygon AOI; the grid footprint is the true projected footprint.
- A first version of the mask overlay painted the whole footprint (GDAL changed nodata-colliding 0s); caught by a new test, fixed.
- STAC read used `from_bounds` with the grid CRS on assets in their own CRS (wrong across UTM zones); now `WarpedVRT` via `read_to_grid`. Tile id and date range were hard-coded; now read from the item/arguments.
- **Sentinel-2 L2A reflectance ignored the −1000 DN BOA offset** (baseline ≥ 04.00, all bundled scenes). MNDWI and the SWIR bare-ground test were biased. Corrected (rule recorded per scene). Effect on the legacy Chennai grid: loss 43.97 ha → 32.65 ha, class E2 → E1, mean SWIR1 0.26 → 0.12. Earlier figures in `BUILD_STATUS.md` are superseded.
- Case coordinates used the analysis-grid centre; now the centroid of the flagged area when one exists.
- Jurisdiction was matched by grid-origin proximity and labelled "verified"; now explicit context with its source status ("unverified" for the legacy label).
- Synthetic fixtures were named after real places (Solapur, Vijayawada, Pulicat); replaced by one synthetic grid over open ocean.
- `CS_MODE` inline-comment hack replaced by validation; `.env.example` has no inline comments.
- Result wording "Suspected encroachment" → "Potential encroachment indicator".

## 2026-10-31 — integration and stabilization handoff

- Added optional Earth Engine adapters for Dynamic World water/built/bare probabilities and JRC historical occurrence/seasonality/recurrence. Results are aligned to the requested projected grid and retain dataset/period/retrieval provenance.
- Connected Dynamic World/JRC retrieval to registry-AOI runs as evidence metadata only; explicitly excluded them from heuristic scoring pending calibration.
- Added optional Neo4j graph mirror, SMTP PDF case alerts, and opt-in Sarvam summary translation in the case review UI.
- Kept SQLite as the system of record; external integration failures do not invalidate saved cases.
- Fixed Affine transform compatibility in the cross-UTM cache test.
- Offline tests: 70 passed, 1 skipped (Streamlit smoke excluded because Streamlit could not be installed in the offline build environment).


## 2026-10-31 — Base application refinement

- Added an operational dashboard for case metrics, geographic readiness, source readiness and recent cases.
- Renamed navigation to Dashboard, Pilot registry, Analysis workspace, Case management, and Methods and readiness.
- Removed the in-app Sarvam translation action from the base workflow; translation is deferred.
- Removed live adapter discovery from the registry screen; source readiness is now presented without implying live integration.
- Clarified that the local operator label is audit attribution only and is not authentication.
- Rewrote release documentation to separate working base features from deferred integrations and operational blockers.
