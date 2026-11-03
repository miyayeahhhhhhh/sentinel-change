import json
import requests
import geopandas as gpd
from shapely.geometry import shape, Polygon, MultiPolygon, mapping

# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DISTRICT_FILE = BASE_DIR / "hyderabad_district.geojson"
OUTPUT_FILE = BASE_DIR / "hyderabad_waterbodies.geojson"
LAYER_URL = (
    "https://tgrac.telangana.gov.in/arcgis/rest/services/"
    "TGIIC_Folder/BaseLayers/MapServer/14"
)



# --------------------------------------------------
# 1. LOAD HYDERABAD DISTRICT
# --------------------------------------------------

district = gpd.read_file(DISTRICT_FILE).to_crs("EPSG:4326")
district_geometry = district.geometry.union_all()

print("Hyderabad district loaded.")

# --------------------------------------------------
# 2. FIND WATERBODY IDS IN DISTRICT BOUNDING BOX
# --------------------------------------------------

minx, miny, maxx, maxy = district_geometry.bounds

params = {
    "where": "1=1",
    "geometry": json.dumps({
        "xmin": minx,
        "ymin": miny,
        "xmax": maxx,
        "ymax": maxy
    }),
    "geometryType": "esriGeometryEnvelope",
    "inSR": 4326,
    "spatialRel": "esriSpatialRelIntersects",
    "returnIdsOnly": "true",
    "f": "json"
}

response = requests.get(
    f"{LAYER_URL}/query",
    params=params,
    timeout=120
)
response.raise_for_status()

result = response.json()

if "error" in result:
    raise RuntimeError(result["error"])

object_ids = result.get("objectIds", [])

print(f"Waterbody records found in search area: {len(object_ids)}")

if not object_ids:
    raise RuntimeError("No waterbody records found in the search area.")

# --------------------------------------------------
# 3. DOWNLOAD POLYGONS IN SMALL BATCHES
# --------------------------------------------------

features = []

for start in range(0, len(object_ids), 100):
    batch = object_ids[start:start + 100]

    params = {
        "objectIds": ",".join(map(str, batch)),
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,
        "f": "json"
    }

    response = requests.get(
        f"{LAYER_URL}/query",
        params=params,
        timeout=120
    )
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(data["error"])

    features.extend(data.get("features", []))

    print(f"Downloaded {len(features)} / {len(object_ids)}")

# --------------------------------------------------
# 4. CONVERT ARCGIS POLYGONS TO SHAPELY
# --------------------------------------------------

def convert_esri_rings(rings):
    polygons = []

    for ring in rings:
        polygon = Polygon(ring)
        if polygon.is_valid and not polygon.is_empty:
            polygons.append(polygon)

    # Build containment hierarchy for outer rings and holes.
    polygons.sort(key=lambda p: p.area, reverse=True)
    parents = {}

    for i, polygon in enumerate(polygons):
        parent = None

        for j in range(i):
            if polygons[j].contains(polygon):
                if parent is None or polygons[j].area < polygons[parent].area:
                    parent = j

        parents[i] = parent

    depths = {}

    for i in range(len(polygons)):
        depth = 0
        parent = parents[i]

        while parent is not None:
            depth += 1
            parent = parents[parent]

        depths[i] = depth

    result = []

    for i, polygon in enumerate(polygons):
        if depths[i] % 2 == 0:
            holes = [
                list(polygons[j].exterior.coords)
                for j in range(len(polygons))
                if parents[j] == i and depths[j] == depths[i] + 1
            ]

            result.append(
                Polygon(list(polygon.exterior.coords), holes)
            )

    if len(result) == 1:
        return result[0]

    return MultiPolygon(result)


# --------------------------------------------------
# 5. FILTER AGAINST EXACT DISTRICT BOUNDARY
# --------------------------------------------------

records = []

for feature in features:
    attributes = feature.get("attributes", {})
    geometry_data = feature.get("geometry")

    if not geometry_data or not geometry_data.get("rings"):
        continue

    geometry = convert_esri_rings(geometry_data["rings"])

    if geometry.is_empty:
        continue

    if not geometry.is_valid:
        geometry = geometry.buffer(0)

    if geometry.is_empty or geometry.area == 0:
        continue

    if not geometry.intersects(district_geometry):
        continue

    percentage_inside = (
        geometry.intersection(district_geometry).area / geometry.area
    ) * 100

    if percentage_inside >= 90:
        records.append({
            **attributes,
            "geometry": geometry
        })

# --------------------------------------------------
# 6. SAVE GEOJSON
# --------------------------------------------------

if not records:
    raise RuntimeError(
        "No waterbodies passed the 90% district containment check."
    )

gdf = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")

gdf.to_file(OUTPUT_FILE, driver="GeoJSON")

print("\nSUCCESS")
print(f"Waterbodies inside Hyderabad: {len(gdf)}")
print(f"Saved to: {OUTPUT_FILE}")
print("\nWaterbody classifications:")
print(gdf["Descr_2"].value_counts().to_string())