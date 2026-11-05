# Change Sentinel — On-Screen Guide

**Target runtime:** about 2:35–2:45, 1920×1080, 16:9. Keep captions readable, use restrained fades, and leave the narration subtitles on screen long enough to read. Use actual app captures and the real `ENC-0012` outputs; do not recreate a fake interface.

## Scene 1 — The challenge (0:00–0:15)

**Show:** Start on a dark title card, then reveal the real Hussain Sagar AOI and a georeferenced water-frequency map. Use the AOI outline from the case record; do not use stock or invented satellite imagery.

**On-screen text:**
- `Monitoring waterbodies at scale`
- `A shoreline change is an observation — not proof of encroachment`

**Narration:** Scene 1 in `narration_script.txt`.

## Scene 2 — Introduce Change Sentinel (0:15–0:30)

**Show:** Capture the actual Change Sentinel application title/workspace if available. Keep it on the real app; do not build a mock dashboard. If you cannot capture the app, hold on the georeferenced case map with the Change Sentinel name.

**On-screen text:**
- `CHANGE SENTINEL`
- `Satellite-based waterbody change screening`
- `Evidence → traceable case → human review`
- `Decision support · not a legal authority`

**Narration:** Scene 2.

## Scene 3 — Live catalog acquisition (0:30–0:55)

**Show:** Use the real stored acquisition summary from `outputs/ENC-0012/evidence.json`. A real case-detail panel or a clean type card is fine. Include the query date so viewers understand this is a recorded run.

**On-screen text:**
- `Verified study area · Hussain Sagar`
- `Catalog query · 05 Nov 2026`
- `640 found · 353 cloud-filtered · 287 read · 0 failed`
- `Accepted observations · 09 Nov 2022 – 14 Oct 2026`
- `Latest usable observation · 22 days before analysis`
- Small caveat: `Live catalog acquisition; imagery was not same-day`

**Narration:** Scene 3.

## Scene 4 — Seasonal comparison (0:55–1:17)

**Show:** A before/after georeferenced comparison using the real rasters below. Use the same map extent and color scale for both; keep north at the top and show the CRS/grid note. Do not stretch the old analysis-grid PNG and call it a map.

- Before: `outputs/ENC-0012/baseline_matched_frequency.tif`
- After: `outputs/ENC-0012/recent_matched_frequency.tif`

**On-screen text:**
- `Same-calendar-month comparison`
- `Historical-footprint comparison coverage · 100%`
- Legend: `Blue intensity = observed-water frequency`
- `Derived georeferenced evidence · not raw-color imagery`

**Narration:** Scene 4.

## Scene 5 — Observed-change candidate (1:17–1:38)

**Show:** The recent-frequency raster with the actual exported candidate mask in orange, clipped to the AOI. Add a subtle outline and actual centroid. The map must use the GeoTIFF transform/CRS or an alignment-preserving GIS export.

- Recent raster: `outputs/ENC-0012/recent_matched_frequency.tif`
- Candidate mask: `outputs/ENC-0012/change_mask.tif`
- AOI geometry: `outputs/ENC-0012/aoi.geojson`

**On-screen text:**
- `Observed water-extent loss candidate · 51.9 ha`
- `Historical water footprint · 209.29 ha`
- `Seasonally unmatched and suppressed · 28.98 ha`
- `Boundary-area uncertainty · ±14.63 ha`
- `E1 · observed extent loss`
- `Not confirmed encroachment`

**Narration:** Scene 5.

## Scene 6 — Evidence fusion (1:38–1:58)

**Show:** Three simple source cards beside the map. Keep their roles distinct; JRC is historical, while Sentinel-1 and Dynamic World provide current-period evidence in this recorded case.

**On-screen text:**
- `Sentinel-1 · CONTRADICTS · recent radar still reads water`
- `Dynamic World · CONTRADICTS · mean recent water probability 0.70`
- `JRC Global Surface Water · HISTORICAL CONTEXT · record ends 2020`
- `Sources disagree`

**Narration:** Scene 6.

## Scene 7 — Uncertainty and priority (1:58–2:16)

**Show:** A restrained score card and the case state. Keep the candidate map faintly visible. Do not call the score “confidence” or a probability.

**On-screen text:**
- `50 / 100 · heuristic review-priority score`
- `Not a probability or calibrated confidence`
- `State · POTENTIAL CHANGE`
- `No source confirms illegal encroachment`

**Narration:** Scene 7.

## Scene 8 — Traceable case (2:16–2:32)

**Show:** Actual case detail/export view if available, or genuine filenames and values from the case output directory. Do not imply Neo4j investigation features unless you verify them in the running app.

**On-screen text:**
- `Case · ENC-0012`
- `Verified AOI · observation dates · evidence provenance`
- `GeoTIFF · GeoJSON · evidence JSON`
- `Reviewer and field verification required`

**Narration:** Scene 8.

## Scene 9 — Closing (2:32–2:45)

**Show:** Return to the georeferenced Hussain Sagar map, then finish on a clean Change Sentinel title card.

**On-screen text:**
- `From satellite observations to traceable waterbody investigations.`
- `CHANGE SENTINEL`
- Small footer: `Satellite-derived screening · not a legal finding`

**Narration:** Scene 9.

## Editing notes

- Burn in English subtitles and also export an SRT file. Keep subtitles in a consistent lower-third area, away from map legends and metrics.
- Avoid fast zooms, fake map pins, fabricated UI screens, stock satellite images, invented partnerships, or accuracy percentages.
- Do not imply a real-time or same-day image: the catalog query is dated 05 Nov 2026, while the latest accepted observation was 14 Oct 2026.
- The source case reports disagreement: Sentinel-1 and Dynamic World contradict the candidate; JRC supports historical water context only. Preserve that disagreement in the edit.
- Do not show the 50/100 score as the probability that encroachment occurred.
