#!/usr/bin/env python3
"""
Merge TIGER place boundaries, match to cities.json entries,
output polygon + centroid GeoJSON, compute bounds, and regenerate PMTiles.

Embeds avg_nwi and population as feature properties so the map can
style directly from tile data (no 28k-entry match expression needed).

Centroids get a minzoom property based on population tier for
progressive disclosure: big cities appear first as you zoom in.
"""

import json
import math
import subprocess
import os
from pathlib import Path

PLACES_DIR = Path(__file__).parent / "boundaries" / "places_raw"
BOUNDARIES_DIR = Path(__file__).parent / "boundaries"
PUBLIC = Path(__file__).parent.parent / "public"
CITIES_JSON = PUBLIC / "data" / "cities.json"

# State FIPS to name mapping
STATE_FIPS = {
    '01': 'Alabama', '02': 'Alaska', '04': 'Arizona', '05': 'Arkansas', '06': 'California',
    '08': 'Colorado', '09': 'Connecticut', '10': 'Delaware', '11': 'District of Columbia',
    '12': 'Florida', '13': 'Georgia', '15': 'Hawaii', '16': 'Idaho', '17': 'Illinois',
    '18': 'Indiana', '19': 'Iowa', '20': 'Kansas', '21': 'Kentucky', '22': 'Louisiana',
    '23': 'Maine', '24': 'Maryland', '25': 'Massachusetts', '26': 'Michigan', '27': 'Minnesota',
    '28': 'Mississippi', '29': 'Missouri', '30': 'Montana', '31': 'Nebraska', '32': 'Nevada',
    '33': 'New Hampshire', '34': 'New Jersey', '35': 'New Mexico', '36': 'New York',
    '37': 'North Carolina', '38': 'North Dakota', '39': 'Ohio', '40': 'Oklahoma',
    '41': 'Oregon', '42': 'Pennsylvania', '44': 'Rhode Island', '45': 'South Carolina',
    '46': 'South Dakota', '47': 'Tennessee', '48': 'Texas', '49': 'Utah', '50': 'Vermont',
    '51': 'Virginia', '53': 'Washington', '54': 'West Virginia', '55': 'Wisconsin',
    '56': 'Wyoming', '72': 'Puerto Rico',
}

# Population tiers for progressive dot disclosure
POP_TIERS = [
    (100_000, 3),   # 100k+ visible from z3
    (25_000,  5),   # 25k+ from z5
    (5_000,   7),   # 5k+ from z7
    (0,       8),   # all from z8
]


def pop_minzoom(population):
    """Return the minimum zoom at which a city should appear as a dot."""
    for threshold, zoom in POP_TIERS:
        if population >= threshold:
            return zoom
    return 8


def bbox_of_geometry(geom):
    """Compute [minLon, minLat, maxLon, maxLat] from a GeoJSON geometry."""
    coords = []

    def collect(obj):
        if isinstance(obj[0], (int, float)):
            coords.append(obj)
        else:
            for sub in obj:
                collect(sub)

    collect(geom["coordinates"])
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return [min(lons), min(lats), max(lons), max(lats)]


def centroid_of_bbox(bbox):
    """Simple centroid from bounding box. Good enough for dot placement."""
    return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]


def main():
    # Load cities data for matching + score/population embedding
    with open(CITIES_JSON) as f:
        cities = json.load(f)

    # Build lookup: (name_lower, state_fips) -> cities.json key
    city_lookup = {}
    for key, city in cities.items():
        name = city["name"]  # "Missoula, Montana"
        parts = name.rsplit(", ", 1)
        if len(parts) == 2:
            city_name, state_name = parts
            for fips, sname in STATE_FIPS.items():
                if sname == state_name:
                    city_lookup[(city_name.lower(), fips)] = key
                    break

    # Manual overrides for consolidated city-counties
    MANUAL_MATCHES = {
        ("nashville-davidson metropolitan government (balance)", "47"): "Nashville|47",
        ("louisville/jefferson county metro government (balance)", "21"): "Louisville|21",
        ("indianapolis city (balance)", "18"): "Indianapolis|18",
        ("lexington-fayette", "21"): "Lexington|21",
        ("augusta-richmond county consolidated government (balance)", "13"): "Augusta|13",
        ("athens-clarke county unified government (balance)", "13"): "Athens|13",
        ("urban honolulu", "15"): "Honolulu|15",
    }

    print(f"Cities to match: {len(city_lookup)} + {len(MANUAL_MATCHES)} manual overrides")

    # Process each state's place shapefile
    matched_features = []    # polygon features
    point_features = []      # centroid features
    bounds = {}              # FIPS -> [minLon, minLat, maxLon, maxLat]
    unmatched_cities = set(city_lookup.keys())

    shapefiles = sorted(PLACES_DIR.glob("tl_2010_*_place10.shp"))
    print(f"Processing {len(shapefiles)} state shapefiles...")

    for shp in shapefiles:
        result = subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", "/dev/stdout", str(shp)],
            capture_output=True
        )
        if result.returncode != 0:
            print(f"  ERROR on {shp.name}: {result.stderr[:100]}")
            continue

        gj = json.loads(result.stdout.decode("latin-1"))
        state_fips = shp.name.split("_")[2]
        state_matched = 0

        for feat in gj["features"]:
            props = feat["properties"]
            place_name = props.get("NAME10", "")
            lookup_key = (place_name.lower(), state_fips)

            cities_key = None
            if lookup_key in city_lookup:
                cities_key = city_lookup[lookup_key]
                unmatched_cities.discard(lookup_key)
            elif lookup_key in MANUAL_MATCHES:
                cities_key = MANUAL_MATCHES[lookup_key]

            if not cities_key:
                continue

            city_data = cities.get(cities_key)
            if not city_data:
                continue

            pop = city_data["population"]
            nwi = city_data["avg_nwi"]

            # Polygon feature — embed score and population
            feat["properties"] = {
                "FIPS": cities_key,
                "NAME": place_name,
                "STATEFP": state_fips,
                "nwi": nwi,
                "pop": pop,
            }
            matched_features.append(feat)

            # Compute bounds
            bbox = bbox_of_geometry(feat["geometry"])
            bounds[cities_key] = [round(v, 4) for v in bbox]

            # Centroid point feature — tippecanoe:minzoom controls when it appears
            center = centroid_of_bbox(bbox)
            point_features.append({
                "type": "Feature",
                "tippecanoe": {"minzoom": pop_minzoom(pop)},
                "geometry": {"type": "Point", "coordinates": center},
                "properties": {
                    "FIPS": cities_key,
                    "NAME": place_name,
                    "STATEFP": state_fips,
                    "nwi": nwi,
                    "pop": pop,
                },
            })
            state_matched += 1

        if state_matched > 0:
            print(f"  {state_fips} ({STATE_FIPS.get(state_fips, '?')}): {state_matched} places")

    print(f"\nMatched: {len(matched_features)}")
    print(f"Unmatched: {len(unmatched_cities)}")
    if unmatched_cities:
        examples = sorted(unmatched_cities)[:20]
        for name, fips in examples:
            key = city_lookup.get((name, fips), "?")
            pop = cities.get(key, {}).get("population", 0)
            print(f"  {name}, {STATE_FIPS.get(fips, fips)} (pop {pop:,})")

    # Write polygon GeoJSON
    places_geojson = {"type": "FeatureCollection", "features": matched_features}
    poly_path = BOUNDARIES_DIR / "places_filtered.geojson"
    with open(poly_path, "w") as f:
        json.dump(places_geojson, f)
    print(f"\nWrote {poly_path} ({len(matched_features)} polygons)")

    # Write centroid points GeoJSON
    points_geojson = {"type": "FeatureCollection", "features": point_features}
    points_path = BOUNDARIES_DIR / "places_points.geojson"
    with open(points_path, "w") as f:
        json.dump(points_geojson, f)
    print(f"Wrote {points_path} ({len(point_features)} points)")

    # Merge new place bounds into existing bounds.json
    bounds_path = PUBLIC / "data" / "bounds.json"
    if bounds_path.exists():
        with open(bounds_path) as f:
            all_bounds = json.load(f)
    else:
        all_bounds = {}

    # Remove old city bounds (pipe-delimited keys), keep state/county (FIPS-only keys)
    all_bounds = {k: v for k, v in all_bounds.items() if "|" not in k}
    all_bounds.update(bounds)
    with open(bounds_path, "w") as f:
        json.dump(all_bounds, f, separators=(",", ":"))
    print(f"Updated bounds.json: {len(all_bounds)} entries ({len(bounds)} places)")

    # Regenerate PMTiles with four layers
    states_gj = BOUNDARIES_DIR / "states_clean.geojson"
    counties_gj = BOUNDARIES_DIR / "counties_clean.geojson"
    pmtiles_out = PUBLIC / "boundaries.pmtiles"

    print("\nGenerating PMTiles...")
    result = subprocess.run([
        "tippecanoe",
        "-o", str(pmtiles_out),
        "-Z", "0", "-z", "12",
        "--no-feature-limit", "--no-tile-size-limit",
        "--coalesce-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "-L", f"states:{states_gj}",
        "-L", f"counties:{counties_gj}",
        "-L", f"places:{poly_path}",
        "-L", f"places_points:{points_path}",
        "--force",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"tippecanoe error: {result.stderr[-500:]}")
    else:
        size = os.path.getsize(pmtiles_out)
        print(f"PMTiles: {size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
