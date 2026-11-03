# Change Sentinel — Base Application

Change Sentinel is a Streamlit geospatial monitoring application for screening **potential waterbody encroachment**. It is an evidence-review aid, not a legal determination or proof of unlawful activity.

This release focuses on a dependable base application. External integrations are deliberately deferred until the core workflows, data quality controls and review process are accepted.

## Base application scope

- **Dashboard:** case counts, review workload, verified AOI readiness, source readiness and recent cases.
- **Pilot registry:** provenance, checksums, geometry validation, boundary status and waterbody AOI status.
- **Analysis workspace:** Sentinel-2 MNDWI persistence workflow, seasonal safeguards, coverage-aware results and map layers.
- **Case management:** review queue, status changes, reviewer notes, formal decisions and append-only audit history.
- **Exports:** PDF case file, GeoTIFF change/coverage rasters, GeoJSON polygons and JSON evidence metadata.
- **Methods and readiness:** method assumptions, capability status and explicit limitations.

## Integration policy

The following are **not activated in this base-app release**: Sentinel-1 acquisition, Dynamic World retrieval, JRC Global Surface Water retrieval, Neo4j mirroring, outbound email and Sarvam AI translation. Their implementation can be added in a later integration phase. Existing optional adapter code is not evidence of a live, tested connection.

Authentication and enforceable Admin / Analyst / Reviewer permissions are also a later milestone. The current local operator label is only an audit attribution field; it is not a login system or a security boundary.

## Evidence integrity rules

- Missing imagery or credentials must result in an explicit unavailable/unassessed state.
- Insufficient coverage and failed processing must never be interpreted as no change.
- Synthetic fixtures are for development/testing only and must remain visibly labelled.
- The legacy Pallikaranai Chennai cache is not Hyderabad pilot evidence.
- No Hyderabad AOI is analysed as a real pilot area unless its geometry and provenance are verified.
- A heuristic score is a review-priority indicator, not a probability or calibrated confidence.
- Satellite indications require human/field review and do not establish ownership, intent or illegality.

## Setup

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
```

Pages: **Dashboard** · **Pilot registry** · **Analysis workspace** · **Case management** · **Methods and readiness**.

## Configuration

Environment variables are documented in `.env.example`: `CS_MODE` (sample|live), `CS_DB_PATH`, `CS_OUTPUT_DIR`, `CS_REGISTRY_DIR`, and `CS_GEE_PROJECT` (reserved for a later integration phase).

## Registering pilot geography

Use only authoritative source files and record their publisher, dataset, URL, licence and retrieval date.

```bash
python -m cs.registry add-district --file <official_district_file> --field <name_column> --value Hyderabad \\
   --name Hyderabad --publisher "<publisher>" --dataset "<title>" --url <official_download_page> --licence "<licence>" --retrieved YYYY-MM-DD

python -m cs.registry add-waterbody --file <official_waterbody_file> --id <waterbody_slug> --name "<waterbody_name>" <same_source_options>
python -m cs.registry status
```

A registry record is not verified merely because it was uploaded. Status is derived from source metadata, official-domain checks, file checksum and geometry/containment validation.

## Output structure

`outputs/<CASE_ID>/` contains generated case artefacts such as `change_mask.tif`, `coverage.tif`, `polygons.geojson`, `evidence.json`, `preview.png` and the on-demand PDF case file. Case records, review decisions, notes and audit events are stored in SQLite.

## Known limitations

See `BUILD_STATUS.md`, `INCOMPLETE.md` and `INTEGRATIONS.md`. The Hyderabad pilot currently has no verified district/waterbody geometry registered. Live Sentinel-2 acquisition has not been externally validated in this build. Threshold calibration and field validation remain necessary before operational use.
