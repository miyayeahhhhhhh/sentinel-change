# Change Sentinel (HackFront India T8.1) - foundation build

Two **independent** review pipelines on a shared platform: flood mapping (Sentinel-1 SAR) and water-body
encroachment (Sentinel-2 L2A). Outputs are *observations for human review*, not legal or damage findings.

> **Sample mode is the only working data path.** Bundled scenes are deterministic SYNTHETIC fixtures
> (`cs/sample_data.py`), labelled as such everywhere. They are not satellite detections and no accuracy is claimed.

## Setup and launch
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py                                    # opens http://localhost:8501
python -m pytest -q                                     # 24 tests
```
Demo path: **Flood workspace** -> Run analysis (default scenario) -> inspect layers, replay slider, coverage layer ->
**Encroachment workspace** -> Run -> compare with the *seasonal only* and *too few observations* scenarios ->
**Review queues** -> open a case, set status, record a decision, prepare the PDF.

## Configuration (environment variables, see `.env.example`)
`CS_MODE` (sample|live), `CS_DB_PATH`, `CS_OUTPUT_DIR`, `CS_GEE_PROJECT`, `CS_CDSE_USER`, `CS_CDSE_PASSWORD`.
Analysis thresholds live in `cs/config.py` (`FloodParams`, `EncroachParams`) and are editable in the UI.

## Live imagery (NOT implemented - scaffold)
`cs/imagery.py::LiveProvider` raises `NotConfigured`. Intended route (per blueprint): register for Earth Engine
(noncommercial), export clipped S1 GRD (VV dB) and S2 L2A (green, SWIR1, SCL) GeoTIFFs for the AOI on a UTM grid, cache with
request parameters + checksum, and return `Scene` objects. CDSE STAC/openEO is the documented fallback. Rasters must share one
projected-metre grid; the pipelines validate this and fail loudly otherwise.

## Outputs per case (`outputs/<CASE_ID>/`)
`change_mask.tif` (0/1, **255 = nodata/unobserved**), `coverage.tif`, `polygons.geojson` (EPSG:4326), `evidence.json`,
`preview.png`, and on demand `<CASE_ID>_case_file.pdf`. Case records live in SQLite (`cases`, append-only `reviewer_decision`,
`notes`, `audit_log`).

## Limitations
Rule-based, uncalibrated thresholds; scores are heuristic review-priority scores, not probabilities. See *Methods and status* page
and `ARCHITECTURE.md`. Not real-time. Raster overlays on the map are axis-aligned display approximations of UTM grids.

## Troubleshooting
- `rasterio`/`geopandas` install errors on Windows: use Python 3.11/3.12 wheels (`pip install --upgrade pip` first) or conda-forge.
- Basemap tiles blank: they need internet; analysis layers still render.
- Reset local state: delete `data/changesentinel.db` and `outputs/`.
