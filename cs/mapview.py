"""Folium map construction. Raster overlays use axis-aligned WGS84 bounds (display approximation only)."""
from __future__ import annotations
import base64
import io
import json

import folium
import numpy as np
from folium import plugins
from PIL import Image

from .models import Result


def _uri(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _mask_rgba(mask, rgb, alpha=0.85):
    o = np.zeros(mask.shape + (4,), np.uint8)
    o[mask, :3], o[mask, 3] = rgb, int(255 * alpha)
    return o


LAYERS = {
    "flood": {"flood": ("Potential floodwater (F1)", (200, 60, 40)), "perm_water": ("Reference/permanent water (F2)", (40, 90, 160))},
    "encroachment": {"loss": ("Suspected extent loss (E1/E2)", (200, 60, 40)), "seasonal": ("Likely seasonal (E3, not flagged)", (214, 158, 46)),
                     "ref_water": ("Historical footprint", (40, 90, 160))},
}


def layer_names(result: Result) -> list[str]:
    return [v[0] for v in LAYERS[result.pipeline.value].values()] + ["Data coverage / gaps", "Replay: water on selected date"]


def build_map(result: Result, shown: list[str], opacity: float = 0.7, replay_idx: int | None = None) -> folium.Map:
    w, s, e, n = result.grid.bounds_wgs84()
    bounds = [[s, w], [n, e]]
    m = folium.Map(location=[(s + n) / 2, (w + e) / 2], zoom_start=14, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery", name="Esri imagery", show=False).add_to(m)
    folium.GeoJson(result.grid.aoi_geojson(), name="AOI", style_function=lambda _: {"color": "#1f4e79", "weight": 2, "fill": False}).add_to(m)

    def add(rgba, name, show):
        folium.raster_layers.ImageOverlay(_uri(rgba), bounds=bounds, opacity=opacity, name=name, show=show, interactive=False).add_to(m)

    a = result.arrays
    for key, (label, rgb) in LAYERS[result.pipeline.value].items():
        if key in a:
            add(_mask_rgba(a[key].astype(bool), rgb), label, label in shown)
    gap = np.zeros(a["coverage"].shape + (4,), np.uint8)
    gap[..., :3] = (70, 80, 90)
    gap[..., 3] = ((1 - np.clip(a["coverage"], 0, 1)) * 190).astype(np.uint8)
    add(gap, "Data coverage / gaps", "Data coverage / gaps" in shown)
    stack = a.get("water_stack")
    if replay_idx is not None and stack is not None and len(stack):
        st = stack[replay_idx]
        rgba = _mask_rgba(st == 1, (30, 110, 190), 0.9)
        rgba[st == 255] = (120, 125, 135, 150)
        add(rgba, "Replay: water on selected date", "Replay: water on selected date" in shown)
    if result.polygons is not None and len(result.polygons):
        g = result.polygons.to_crs(4326)
        folium.GeoJson(json.loads(g.to_json()), name="Flagged polygons", style_function=lambda _: {"color": "#8b2d1f", "weight": 1.5, "fillOpacity": 0},
                       tooltip=folium.GeoJsonTooltip(fields=["area_ha"], aliases=["Area (ha):"])).add_to(m)
    replay_legend = ""
    if "Replay: water on selected date" in shown:
        replay_legend = ("<span style='display:inline-block;width:10px;height:10px;background:rgb(30,110,190);margin-right:5px'></span>Replay date water<br>"
                         "<span style='display:inline-block;width:10px;height:10px;background:rgb(120,125,135);margin-right:5px'></span>Replay unobserved/masked<br>")
    legend = ("<div style='position:fixed;bottom:24px;left:12px;z-index:9999;background:#ffffff;border:1px solid #c5ccd3;"
              "border-radius:2px;padding:8px 11px;font:11px/1.4 -apple-system,BlinkMacSystemFont,\"Segoe UI\",Roboto,sans-serif;color:#1f2933;"
              "box-shadow:0 1px 4px rgba(0,0,0,0.15)'><b>Map Legend</b><br>"
              + "".join(f"<span style='display:inline-block;width:10px;height:10px;background:rgb{rgb};margin-right:5px'></span>{lab}<br>"
                        for lab, rgb in LAYERS[result.pipeline.value].values())
              + replay_legend
              + "<span style='display:inline-block;width:10px;height:10px;background:#46505a;margin-right:5px'></span>Data gap (darker = less data)<br>"
              + ("<span style='color:#8c5b0f;font-size:10px;font-style:italic;'>SYNTHETIC sample fixture</span>" if result.synthetic else "") + "</div>")
    m.get_root().html.add_child(folium.Element(legend))
    plugins.MeasureControl(primary_length_unit="meters").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.fit_bounds(bounds)
    return m
