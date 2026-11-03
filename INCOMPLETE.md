# Incomplete integrations and blockers (as of Extension M3)

| Item | State |
|---|---|
| Hyderabad district + Hussain Sagar | Registered and verified by the user (licence "not stated in source metadata": reuse terms unverified). |
| Sentinel-2 live acquisition | Fixed in M3 (partial caches, per-item failures, pagination, manifest). First real re-run after applying the extension is still needed; the readiness table shows the recorded outcome. |
| Sentinel-1 (Planetary Computer RTC) | Implemented; **never run live**. Run `python scripts/diagnose_live.py --aoi hussain-sagar` and send diagnostics.json to confirm property/asset names and coverage. |
| JRC GSW (Planetary Computer) | Implemented; **never run live**; Earth Engine fallback not implemented; monthly-history seasonality check not implemented. |
| Dynamic World (Earth Engine) | Implemented (tiled, cached); **blocked on credentials** until `earthengine-api`, `CS_GEE_PROJECT` and auth exist; never run live. |
| Thresholds, weights, seasonal profiles | Uncalibrated / assumed; no labelled ground truth yet. |
| DEM/HAND masks, OSM exposure join, field validation | Not implemented. |
