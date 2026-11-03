import geopandas as gpd
import requests

from shapely.geometry import shape, Polygon, MultiPolygon

BASE = "https://tgrac.telangana.gov.in/arcgis/rest/services/TGIIC_Folder/BaseLayers/MapServer"

district = gpd.read_file("hyderabad_district.geojson").to_crs(4326)
waterbodies = gpd.read_file("hyderabad_waterbodies.geojson").to_crs(4326)

district_geom = district.geometry.union_all()
candidate = waterbodies[waterbodies["OBJECTID"] == 16144].geometry.iloc[0]

minx, miny, maxx, maxy = district_geom.bounds

url = BASE + "/16/query"

params = {
    "where": "1=1",
    "geometry": f"{minx},{miny},{maxx},{maxy}",
    "geometryType": "esriGeometryEnvelope",
    "inSR": 4326,
    "spatialRel": "esriSpatialRelIntersects",
    "outFields": "OBJECTID,Name,TYPE,Category,res",
    "returnGeometry": "true",
    "outSR": 4326,
    "f": "json"
}

response = requests.get(url, params=params, timeout=120)
response.raise_for_status()
data = response.json()

if "error" in data:
    raise RuntimeError(data["error"])

results = []

for feature in data.get("features", []):
    attrs = feature["attributes"]
    rings = feature["geometry"]["rings"]

    geom = Polygon(rings[0])

    for ring in rings[1:]:
        hole = Polygon(ring)
        if geom.contains(hole):
            geom = Polygon(
                geom.exterior.coords,
                [hole.exterior.coords]
            )

    if not geom.is_valid:
        geom = geom.buffer(0)

    if geom.is_empty or not geom.intersects(district_geom):
        continue

    overlap = geom.intersection(candidate).area / candidate.area * 100

    results.append({
        "OBJECTID": attrs.get("OBJECTID"),
        "Name": attrs.get("Name"),
        "TYPE": attrs.get("TYPE"),
        "Category": attrs.get("Category"),
        "Overlap_with_16144_percent": round(overlap, 2)
    })

results.sort(
    key=lambda x: x["Overlap_with_16144_percent"],
    reverse=True
)

print("\nNAMED RESERVOIRS IN HYDERABAD DISTRICT\n")

for item in results:
    print(item)

print("\nTotal named reservoir records:", len(results))