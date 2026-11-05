"""Folium map construction with geometrically correct overlays.

Every raster is warped (geo.web_mercator_overlay) to EPSG:3857 before being placed, because a Leaflet ImageOverlay is
stretched linearly in Mercator space; placing a UTM raster by its lon/lat bbox (the old approach) misaligns it.
Rasters are clipped to the polygon AOI. Every layer is its own FeatureGroup so it can be toggled independently.
"""
from __future__ import annotations
import base64
import io
import json

import folium
import numpy as np
import rasterio
from folium import plugins
from PIL import Image

from .geo import Grid, footprint_wgs84, web_mercator_overlay
from .models import Result

REGISTRY_STYLE = {"verified": ("#1f4e79", None), "provisional": ("#b45309", "6 5"), "unverified": ("#64748b", "6 5"),
                  "invalid": ("#991b1b", "2 4")}
EVIDENCE = {"loss": ("Potential extent loss (E1/E2)", (200, 60, 40)),
            "seasonal": ("Likely seasonal (E3, not flagged)", (214, 158, 46)),
            "ref_water": ("Historical water footprint (S2 baseline)", (40, 90, 160))}
L_DISTRICT, L_WB, L_AOI, L_GRID = "Pilot district boundary", "Waterbody boundary", "Analysis AOI (polygon)", "Analysis grid footprint"
L_IMG = "Sentinel-2 MNDWI, latest usable scene (derived index)"
L_COV, L_GAP, L_REPLAY = "Observation coverage (share of recent passes valid)", "Data gaps (unobserved)", "Replay: water on selected date"
L_POLY = "Flagged polygons"
# independent evidence layers (only present when that source was actually assessed): label -> (array key, predicate, rgb)
SOURCE_LAYERS = {
    "Sentinel-1: SAR water loss": ("sar_water_loss", lambda x: x == 1, (120, 60, 160)),
    "Dynamic World: water to built/bare": ("dw_transition", lambda x: (x == 1) | (x == 2), (190, 90, 30)),
    "JRC: historically permanent water": ("jrc_class", lambda x: x == 2, (20, 130, 150)),
    "JRC: historically seasonal water": ("jrc_class", lambda x: x == 1, (150, 190, 200)),
}
DEFAULT_ON = {L_WB, L_AOI, L_GRID, EVIDENCE["loss"][0], L_POLY, L_GAP}


def default_layers(result: Result, names: list[str]) -> list[str]:
    """Layers shown initially. For a case that was not assessed there is no analysis to paint: coverage/gap layers would
    only darken the whole AOI, so they start off (the workspace explains why instead)."""
    drop = {L_GAP, L_COV} if result.state.value in ("not_assessed", "analysis_failed") else set()
    return [n for n in names if n in DEFAULT_ON and n not in drop]


def _uri(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _mask_overlay(mask: np.ndarray, grid: Grid, rgb, alpha: float = 0.85):
    warped, bounds = web_mercator_overlay(mask.astype("uint8"), grid, "nearest", nodata=255)   # 0/1 are data; 255 = outside
    rgba = np.zeros(warped.shape + (4,), np.uint8)
    on = warped == 1
    rgba[on, :3], rgba[on, 3] = rgb, int(255 * alpha)
    return rgba, bounds


def _ramp(v: np.ndarray, lo: float, hi: float, c0, c1) -> np.ndarray:
    t = np.clip((np.nan_to_num(v, nan=lo) - lo) / (hi - lo), 0, 1)[..., None]
    return (np.array(c0) * (1 - t) + np.array(c1) * t).astype(np.uint8)


def layer_catalog(result: Result, boundaries: dict | None = None) -> dict:
    """{group: [layer names]} for layers that actually have data in this result/context."""
    b = boundaries or {}
    a = result.arrays
    cat = {"Boundaries": [], "Source imagery": [], "Observation coverage": [], "Analysis evidence": []}
    if b.get("district") is not None and b["district"].geometry is not None:
        cat["Boundaries"].append(L_DISTRICT)
    if b.get("waterbody") is not None and b["waterbody"].geometry is not None:
        cat["Boundaries"].append(L_WB)
    if (result.evidence.get("aoi") or {}).get("geometry_geojson"):
        cat["Boundaries"].append(L_AOI)
    cat["Boundaries"].append(L_GRID)
    if "context" in a and np.isfinite(a["context"]).any():
        cat["Source imagery"].append(L_IMG)
    cat["Observation coverage"] += [L_COV, L_GAP]
    if a.get("water_stack") is not None and len(a["water_stack"]):
        cat["Observation coverage"].append(L_REPLAY)
    cat["Analysis evidence"] += [v[0] for k, v in EVIDENCE.items() if k in a]
    cat["Analysis evidence"] += [lab for lab, (k, _, _) in SOURCE_LAYERS.items() if k in a]
    if result.polygons is not None and len(result.polygons):
        cat["Analysis evidence"].append(L_POLY)
    return cat


def layer_names(result: Result, boundaries: dict | None = None) -> list:
    return [n for names in layer_catalog(result, boundaries).values() for n in names]


def _record_layer(rec, label: str, fill: bool) -> folium.GeoJson:
    colour, dash = REGISTRY_STYLE[rec.status]
    if getattr(rec, "kind", "") == "district":
        dash = "10 7"                      # district outline is always dashed
    elif getattr(rec, "kind", "") == "waterbody":
        colour = "#e8590c"                 # waterbody boundary: contrasting orange over blue water and grey basemap
    src = rec.source.get("publisher", "source not recorded")
    return folium.GeoJson(
        json.loads(json.dumps(rec.geometry.__geo_interface__)), name=label,
        style_function=lambda _: {"color": colour, "dashArray": dash, "fill": fill, "fillOpacity": 0.08, "weight": 3 if getattr(rec, "kind", "") == "waterbody" else 2},
        tooltip=f"{rec.name} - boundary status: {rec.status} (source: {src})")


def build_map(result: Result, shown: list[str], opacity: float = 0.7, replay_idx: int | None = None,
              boundaries: dict | None = None) -> folium.Map:
    b, a, grid = boundaries or {}, result.arrays, result.grid
    fp = footprint_wgs84(grid)
    w, s, e, n = fp.bounds
    m = folium.Map(location=[(s + n) / 2, (w + e) / 2], zoom_start=14, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery", name="Esri imagery", show=False).add_to(m)
    cat = layer_catalog(result, boundaries)
    aoi_mask = a.get("aoi", np.ones((grid.height, grid.width), bool)).astype(bool)
    groups: dict[str, list] = {g: [] for g in cat}

    def fg(group: str, label: str) -> folium.FeatureGroup:
        f = folium.FeatureGroup(name=label, show=label in shown, control=False)
        groups[group].append(f)
        return f

    def raster(group: str, label: str, rgba, bounds):
        f = fg(group, label)
        folium.raster_layers.ImageOverlay(_uri(rgba), bounds=bounds, opacity=opacity, interactive=False).add_to(f)
        f.add_to(m)

    for label in cat["Boundaries"]:
        f = fg("Boundaries", label)
        if label == L_DISTRICT:
            _record_layer(b["district"], label, False).add_to(f)
        elif label == L_WB:
            _record_layer(b["waterbody"], label, True).add_to(f)
        elif label == L_AOI:
            folium.GeoJson(result.evidence["aoi"]["geometry_geojson"], name=label, tooltip=f"AOI status: {result.evidence['aoi'].get('status')}",
                           style_function=lambda _: {"color": "#0f766e", "weight": 2, "fill": False}).add_to(f)
        else:
            folium.GeoJson(json.loads(json.dumps(fp.__geo_interface__)), name=label,
                           tooltip="Raster analysis grid footprint (projected-grid extent, not an administrative or waterbody boundary)",
                           style_function=lambda _: {"color": "#475569", "weight": 1, "dashArray": "3 4", "fill": False}).add_to(f)
        f.add_to(m)

    if L_IMG in cat["Source imagery"]:
        ctx = np.where(aoi_mask, a["context"], np.nan).astype("float32")
        warped, bounds = web_mercator_overlay(ctx, grid, "bilinear", nodata=np.nan)
        rgba = np.zeros(warped.shape + (4,), np.uint8)
        rgba[..., :3] = _ramp(warped, -0.6, 0.6, (150, 120, 80), (30, 110, 190))
        rgba[..., 3] = np.where(np.isfinite(warped), 255, 0)
        raster("Source imagery", L_IMG, rgba, bounds)

    cov = np.where(aoi_mask, np.clip(a["coverage"], 0, 1), np.nan).astype("float32")
    warped, bounds = web_mercator_overlay(cov, grid, "bilinear", nodata=np.nan)
    rgba = np.zeros(warped.shape + (4,), np.uint8)
    rgba[..., :3] = _ramp(warped, 0.0, 1.0, (220, 235, 220), (20, 110, 60))
    rgba[..., 3] = np.where(np.isfinite(warped), 200, 0)
    raster("Observation coverage", L_COV, rgba, bounds)
    gap = np.where(aoi_mask, 1 - np.clip(a["coverage"], 0, 1), np.nan).astype("float32")
    warped, bounds = web_mercator_overlay(gap, grid, "bilinear", nodata=np.nan)
    rgba = np.zeros(warped.shape + (4,), np.uint8)
    rgba[..., :3] = (70, 80, 90)
    rgba[..., 3] = np.where(np.isfinite(warped), np.nan_to_num(warped) * 190, 0).astype(np.uint8)
    raster("Observation coverage", L_GAP, rgba, bounds)

    if L_REPLAY in cat["Observation coverage"] and replay_idx is not None:
        st = np.where(aoi_mask, a["water_stack"][replay_idx], 254).astype("uint8")
        warped, bounds = web_mercator_overlay(st, grid, "nearest", nodata=254)
        rgba = np.zeros(warped.shape + (4,), np.uint8)
        rgba[warped == 1] = (30, 110, 190, 230)
        rgba[warped == 255] = (120, 125, 135, 150)
        raster("Observation coverage", L_REPLAY, rgba, bounds)

    for key, (label, rgb) in EVIDENCE.items():
        if key in a:
            rgba, bounds = _mask_overlay(a[key].astype(bool) & aoi_mask, grid, rgb)
            raster("Analysis evidence", label, rgba, bounds)
    for lab, (k, pred, rgb) in SOURCE_LAYERS.items():
        if lab in cat["Analysis evidence"]:
            arr = a[k]
            rgba, bounds = _mask_overlay(pred(arr) & (arr != 255) & aoi_mask, grid, rgb)
            raster("Analysis evidence", lab, rgba, bounds)
    if L_POLY in cat["Analysis evidence"]:
        f = fg("Analysis evidence", L_POLY)
        folium.GeoJson(json.loads(result.polygons.to_crs(4326).to_json()), name=L_POLY,
                       style_function=lambda _: {"color": "#8b2d1f", "weight": 1.5, "fillOpacity": 0},
                       tooltip=folium.GeoJsonTooltip(fields=[c for c in ("area_ha", "sar_support", "dw_support", "jrc_permanent") if c in result.polygons.columns],
                                                      aliases=[{"area_ha": "Area (ha):", "sar_support": "SAR support:", "dw_support": "Dynamic World support:", "jrc_permanent": "JRC permanent:"}[c]
                                                               for c in ("area_ha", "sar_support", "dw_support", "jrc_permanent") if c in result.polygons.columns])).add_to(f)
        f.add_to(m)

    legend = ("<div style='position:fixed;bottom:24px;left:12px;z-index:9999;background:#fff;border:1px solid #c5ccd3;border-radius:2px;"
              "padding:8px 11px;font:11px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;color:#1f2933;box-shadow:0 1px 4px rgba(0,0,0,.15)'>"
              "<b>Potential-encroachment indicators</b><br>"
              + "".join(f"<span style='display:inline-block;width:10px;height:10px;background:rgb{rgb};margin-right:5px'></span>{lab}<br>"
                        for lab, rgb in EVIDENCE.values())
              + "<span style='display:inline-block;width:10px;height:10px;background:#46505a;margin-right:5px'></span>Data gap (darker = less data)<br>"
              + "<span style='color:#475569;font-size:10px'>Dashed boundary = not a verified source</span><br>"
              + ("<span style='color:#8c5b0f;font-size:10px;font-style:italic'>SYNTHETIC sample fixture</span>" if result.synthetic else "")
              + "</div>")
    m.get_root().html.add_child(folium.Element(legend))
    plugins.MeasureControl(primary_length_unit="meters").add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)                                  # basemaps
    plugins.GroupedLayerControl(groups={g: fgs for g, fgs in groups.items() if fgs}, exclusive_groups=False,
                                collapsed=True).add_to(m)
    m.fit_bounds([[s, w], [n, e]])
    return m


def build_frequency_comparison_map(baseline_path, recent_path) -> folium.Map:
    """Display same-grid, georeferenced baseline/recent observed-water frequency exports."""
    with rasterio.open(baseline_path) as before_src, rasterio.open(recent_path) as after_src:
        if (before_src.crs != after_src.crs or before_src.transform != after_src.transform or
                (before_src.height, before_src.width) != (after_src.height, after_src.width)):
            raise ValueError("Baseline and recent frequency rasters are not spatially aligned")
        grid = Grid(before_src.transform, before_src.crs, before_src.height, before_src.width)
        before = before_src.read(1).astype("float32")
        after = after_src.read(1).astype("float32")
        before[before == before_src.nodata] = np.nan
        after[after == after_src.nodata] = np.nan

    w, s, e, n = grid.bounds_wgs84()
    m = folium.Map(location=[(s + n) / 2, (w + e) / 2], zoom_start=14, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery", name="Esri imagery", show=False).add_to(m)
    for label, values in (("Before · same-month baseline water frequency", before),
                          ("After · recent water frequency (same months)", after)):
        warped, bounds = web_mercator_overlay(values, grid, "bilinear", nodata=np.nan)
        rgba = np.zeros(warped.shape + (4,), np.uint8)
        rgba[..., :3] = _ramp(warped, 0.0, 1.0, (235, 242, 247), (15, 90, 180))
        rgba[..., 3] = np.where(np.isfinite(warped), 220, 0)
        folium.raster_layers.ImageOverlay(_uri(rgba), bounds=bounds, opacity=0.8, name=label,
                                           interactive=False, show=label.startswith("Before")).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds([[s, w], [n, e]])
    return m
