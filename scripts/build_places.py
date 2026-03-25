#!/usr/bin/env python3
"""
Merge TIGER place boundaries, filter to our 500 cities,
output GeoJSON and regenerate PMTiles.
"""

import json
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


def main():
    # Load our cities data to know which cities to keep
    with open(CITIES_JSON) as f:
        cities = json.load(f)

    # Build lookup: (name_lower, state_fips) -> cities.json key
    city_lookup = {}
    for key, city in cities.items():
        name = city["name"]  # "Missoula, Montana"
        parts = name.rsplit(", ", 1)
        if len(parts) == 2:
            city_name, state_name = parts
            # Find state FIPS from name
            for fips, sname in STATE_FIPS.items():
                if sname == state_name:
                    city_lookup[(city_name.lower(), fips)] = key
                    break

    # Manual overrides for consolidated city-counties
    # Census place names differ from our city names for these
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
    matched_features = []
    unmatched_cities = set(city_lookup.keys())

    shapefiles = sorted(PLACES_DIR.glob("tl_2010_*_place10.shp"))
    print(f"Processing {len(shapefiles)} state shapefiles...")

    for shp in shapefiles:
        # Convert to GeoJSON via ogr2ogr (binary mode for encoding safety)
        result = subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", "/dev/stdout", str(shp)],
            capture_output=True
        )
        if result.returncode != 0:
            print(f"  ERROR on {shp.name}: {result.stderr[:100]}")
            continue

        gj = json.loads(result.stdout.decode("latin-1"))
        state_fips = shp.name.split("_")[2]

        for feat in gj["features"]:
            props = feat["properties"]
            place_name = props.get("NAME10", "")
            lookup_key = (place_name.lower(), state_fips)

            # Try exact match first
            cities_key = None
            if lookup_key in city_lookup:
                cities_key = city_lookup[lookup_key]
                unmatched_cities.discard(lookup_key)
            # Try manual overrides
            elif lookup_key in MANUAL_MATCHES:
                cities_key = MANUAL_MATCHES[lookup_key]

            if cities_key:
                feat["properties"] = {
                    "FIPS": cities_key,
                    "NAME": place_name,
                    "STATEFP": state_fips,
                }
                matched_features.append(feat)

    print(f"\nMatched: {len(matched_features)}")
    print(f"Unmatched: {len(unmatched_cities)}")
    if unmatched_cities:
        examples = list(unmatched_cities)[:10]
        for name, fips in examples:
            print(f"  {name}, {STATE_FIPS.get(fips, fips)}")

    # Write filtered places GeoJSON
    places_geojson = {
        "type": "FeatureCollection",
        "features": matched_features,
    }
    out_path = BOUNDARIES_DIR / "places_filtered.geojson"
    with open(out_path, "w") as f:
        json.dump(places_geojson, f)
    print(f"\nWrote {out_path} ({len(matched_features)} features)")

    # Regenerate PMTiles with all three layers
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
        "-L", f"places:{out_path}",
        "--force",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"tippecanoe error: {result.stderr[-500:]}")
    else:
        size = os.path.getsize(pmtiles_out)
        print(f"PMTiles: {size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
