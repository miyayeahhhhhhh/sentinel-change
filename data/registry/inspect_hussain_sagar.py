
import requests
import geopandas as gpd
from pathlib import Path

BASE = (
    "https://tgrac.telangana.gov.in/arcgis/rest/services/"
    "TGIIC_Folder/BaseLayers/MapServer/16"
)

params = {
    "where": "OBJECTID=56",
    "outFields": "*",
    "returnGeometry": "true",
    "outSR": 4326,
    "f": "json"
}

response = requests.get(
    BASE + "/query",
    params=params,
    timeout=120
)

print("HTTP status:", response.status_code)

data = response.json()

if "error" in data:
    print("ArcGIS error:", data["error"])
    raise SystemExit

features = data.get("features", [])

if not features:
    print("No Hussain Sagar feature returned.")
    raise SystemExit

# Convert Esri JSON geometry into GeoJSON features
geojson = {
    "type": "FeatureCollection",
    "features": []
}

for feature in features:
    attrs = feature["attributes"]
    rings = feature["geometry"]["rings"]

    geojson["features"].append({
        "type": "Feature",
        "properties": attrs,
        "geometry": {
            "type": "Polygon",
            "coordinates": rings
        }
    })

output = Path("hussain_sagar.geojson")

import json
output.write_text(json.dumps(geojson), encoding="utf-8")

# Read polygon and district
gdf = gpd.read_file(output).to_crs("EPSG:4326")

district = gpd.read_file(
    "hyderabad_district.geojson"
).to_crs("EPSG:4326")

geom = gdf.geometry.iloc[0]
district_geom = district.geometry.union_all()

if not geom.is_valid:
    geom = geom.buffer(0)

inside = (
    geom.intersection(district_geom).area / geom.area
) * 100

# Calculate area using a suitable projected CRS
projected = gdf.to_crs("EPSG:32644")
area_hectares = projected.geometry.area.iloc[0] / 10000

print("\nHUSSAIN SAGAR RESULTS")
print("Name:", gdf.iloc[0].get("Name"))
print("OBJECTID:", gdf.iloc[0].get("OBJECTID"))
print("Area (hectares):", round(area_hectares, 2))
print("Inside Hyderabad district (%):", round(inside, 2))
print("Geometry valid:", geom.is_valid)
print("Saved:", output.resolve())
