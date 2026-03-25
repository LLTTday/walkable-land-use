#!/usr/bin/env python3
"""
Build block-group-level PMTiles with NWI scores embedded.

1. Merge state-level Census 2010 cartographic boundary shapefiles
2. Join NWI scores from the database (by geoid)
3. Output GeoJSON with NWI score as a property
4. Run tippecanoe to generate PMTiles

The NWI score is embedded in each feature so MapLibre can render
the choropleth with a simple step expression — no client-side join.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

DB = Path(__file__).parent.parent.parent / "nwi_analysis" / "data" / "nwi_full_2019_complete.db"
BG_DIR = Path(__file__).parent / "boundaries" / "bg_raw"
OUT_DIR = Path(__file__).parent / "boundaries"
PUBLIC = Path(__file__).parent.parent / "public"

# Only include zoom levels 6-12 for block groups
# (they're too small to see below zoom 6)
MIN_ZOOM = 6
MAX_ZOOM = 12


def load_nwi_scores():
    """Load NWI scores from database, keyed by geoid (12-digit block group)."""
    conn = sqlite3.connect(str(DB))
    rows = conn.execute("""
        SELECT geoid10,
               CAST(nwi AS INTEGER) as nwi_level,
               CAST(nwi_scaled_10 AS REAL) as nwi_score
        FROM nwi_full
        WHERE geography_type = 'block_group'
    """).fetchall()
    conn.close()

    scores = {}
    for geoid, nwi_level, nwi_score in rows:
        scores[geoid] = (nwi_level, nwi_score or 0)

    print(f"Loaded {len(scores):,} NWI scores")
    return scores


def main():
    scores = load_nwi_scores()

    # Find all state shapefiles
    shapefiles = sorted(BG_DIR.glob("gz_2010_*_150_00_500k.shp"))
    print(f"Found {len(shapefiles)} state shapefiles")

    # Process each state and collect features
    all_features = []
    matched = 0
    unmatched = 0

    for shp in shapefiles:
        state_fips = shp.name.split("_")[2]
        result = subprocess.run(
            ["ogr2ogr", "-f", "GeoJSON", "-t_srs", "EPSG:4326", "/dev/stdout", str(shp)],
            capture_output=True
        )
        if result.returncode != 0:
            print(f"  ERROR: {shp.name}")
            continue

        gj = json.loads(result.stdout.decode("latin-1"))

        for feat in gj["features"]:
            props = feat["properties"]
            # Build 12-digit geoid from state + county + tract + block group
            geo_id = props.get("GEO_ID", "")
            # GEO_ID format: "1500000US010010201001" — last 12 chars are the FIPS
            if "US" in geo_id:
                geoid = geo_id.split("US")[1]
            else:
                # Fallback: construct from components
                geoid = f"{props.get('STATE', '')}{props.get('COUNTY', '')}{props.get('TRACT', '')}{props.get('BLKGRP', '')}"

            nwi_data = scores.get(geoid)
            if nwi_data:
                nwi_level, nwi_score = nwi_data
                feat["properties"] = {
                    "nwi": nwi_level,
                    "s": round(nwi_score, 1),  # short key to save space
                }
                all_features.append(feat)
                matched += 1
            else:
                unmatched += 1

        print(f"  {state_fips}: {len(gj['features'])} BGs")

    print(f"\nMatched: {matched:,}, Unmatched: {unmatched:,}")

    # Write merged GeoJSON
    merged = {"type": "FeatureCollection", "features": all_features}
    merged_path = OUT_DIR / "blockgroups.geojson"
    print(f"Writing {merged_path} ({len(all_features):,} features)...")

    # Write in chunks to avoid memory issues
    with open(merged_path, "w") as f:
        f.write('{"type":"FeatureCollection","features":[\n')
        for i, feat in enumerate(all_features):
            if i > 0:
                f.write(",\n")
            json.dump(feat, f, separators=(",", ":"))
        f.write("\n]}")

    file_size = os.path.getsize(merged_path) / 1024 / 1024
    print(f"GeoJSON: {file_size:.1f} MB")

    # Generate PMTiles — block groups only
    # Aggressive simplification to keep file size reasonable for web serving
    pmtiles_path = PUBLIC / "blockgroups.pmtiles"
    print(f"\nRunning tippecanoe...")
    result = subprocess.run([
        "tippecanoe",
        "-o", str(pmtiles_path),
        "-Z", str(MIN_ZOOM),
        "-z", str(MAX_ZOOM),
        "-l", "blockgroups",
        "--coalesce-densest-as-needed",
        "--drop-densest-as-needed",
        "--simplification=10",  # aggressive geometry simplification
        "--detect-shared-borders",  # prevent gaps between adjacent polygons
        str(merged_path),
        "--force",
    ], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"tippecanoe error: {result.stderr[-500:]}")
        return

    pmtiles_size = os.path.getsize(pmtiles_path) / 1024 / 1024
    print(f"PMTiles: {pmtiles_size:.1f} MB")

    # Clean up large GeoJSON
    print(f"\nCleaning up {merged_path.name}...")
    os.remove(merged_path)
    print("Done.")


if __name__ == "__main__":
    main()
