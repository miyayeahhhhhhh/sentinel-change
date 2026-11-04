from __future__ import annotations
import json
from pathlib import Path
import folium
import streamlit as st
from streamlit_folium import st_folium

def _obj(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try: return json.loads(value)
        except (TypeError, ValueError): return {}
    return {}

def render_case_map(case: dict, exports: dict, settings, registry_module) -> None:
    # Use georeferenced GeoJSON and registered boundaries, never the ungeoreferenced PNG.
    evidence = _obj(case.get("evidence"))
    aoi = _obj(evidence.get("aoi"))
    params = _obj(case.get("params"))
    aoi_id = aoi.get("id") or params.get("aoi_id")
    registry = registry_module.Registry.load(settings.registry_dir)
    waterbody = None
    if aoi_id:
        try: waterbody = registry.waterbody(aoi_id)
        except (KeyError, ValueError, AttributeError): waterbody = None
    district = getattr(registry, "district", None)
    wb_geom = getattr(waterbody, "geometry", None)
    district_geom = getattr(district, "geometry", None)

    polygon_path = Path(exports.get("polygons_geojson", ""))
    fc = {}
    if polygon_path.is_file():
        fc = _obj(polygon_path.read_text(encoding="utf-8"))
    features = fc.get("features", []) if isinstance(fc, dict) else []
    features = [f for f in features if isinstance(f, dict) and f.get("geometry")]
    if wb_geom is None and not features:
        st.info("No registered waterbody boundary or candidate geometry is available for this case. Check its AOI ID and registry.")
        return

    if wb_geom is not None:
        minx, miny, maxx, maxy = wb_geom.bounds
    else:
        from shapely.geometry import shape
        bounds = [shape(f["geometry"]).bounds for f in features]
        minx=min(b[0] for b in bounds); miny=min(b[1] for b in bounds)
        maxx=max(b[2] for b in bounds); maxy=max(b[3] for b in bounds)

    fmap = folium.Map(location=[(miny+maxy)/2, (minx+maxx)/2], zoom_start=14,
                      tiles="OpenStreetMap", control_scale=True, prefer_canvas=True)
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Satellite imagery", show=False
    ).add_to(fmap)
    if district_geom is not None:
        folium.GeoJson(district_geom.__geo_interface__, name="Hyderabad district",
                       style_function=lambda _: {"color":"#64748b","weight":1.5,"fill":False}).add_to(fmap)
    if wb_geom is not None:
        folium.GeoJson(wb_geom.__geo_interface__, name="Hussain Sagar reference boundary",
                       style_function=lambda _: {"color":"#1677c8","weight":3,"fillColor":"#38a5df","fillOpacity":0.12},
                       tooltip="Registered waterbody boundary").add_to(fmap)
    if features:
        folium.GeoJson({"type":"FeatureCollection","features":features},
                       name="Candidate change polygons",
                       style_function=lambda _: {"color":"#c2410c","weight":2.5,"fillColor":"#f97316","fillOpacity":0.45},
                       tooltip=folium.GeoJsonTooltip(fields=list((features[0].get("properties") or {}).keys())[:4])
                       if (features[0].get("properties") or {}) else None).add_to(fmap)
    else:
        st.info("No candidate polygons are stored for this case. The map shows the registered boundary only; no encroachment is implied.")
    folium.LayerControl(collapsed=False).add_to(fmap)
    fmap.fit_bounds([[miny,minx],[maxy,maxx]])
    st_folium(fmap, height=520, use_container_width=True, returned_objects=[])
    st.caption("Georeferenced registry and GeoJSON layers. Candidate polygons are satellite-derived indications for human verification, not legal findings.")
