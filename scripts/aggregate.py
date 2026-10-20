#!/usr/bin/env python3
"""
Aggregate block group data into jurisdiction-level JSON for the v2 tool.

For each geographic level (state, county, city, CSA), produces:
- Population by NWI level (0-3)
- Population-weighted average NWI score
- Demographic breakdowns per NWI level (race, income, homeownership, transportation)

Output: public/data/{states,counties,cities,csas}.json
"""

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).parent.parent.parent / "nwi_analysis" / "data" / "nwi_full_2019_complete.db"
OUT = Path(__file__).parent.parent / "public" / "data"
BOUNDARIES = Path(__file__).parent / "boundaries"

# Demographic columns to aggregate — must match corrected config.py
DEMOGRAPHICS = {
    "race": {
        "white": "b02001_002e",
        "black": "b02001_003e",
        "native_american": "b02001_004e",
        "asian": "b02001_005e",
        "pacific_islander": "b02001_006e",
        "other": "b02001_007e",
        "two_or_more": "b02001_008e",
    },
    "income": {
        "under_25k": ["b19001_002e", "b19001_003e", "b19001_004e", "b19001_005e"],
        "25k_50k": ["b19001_006e", "b19001_007e", "b19001_008e", "b19001_009e", "b19001_010e"],
        "50k_100k": ["b19001_011e", "b19001_012e", "b19001_013e"],
        "over_100k": ["b19001_014e", "b19001_015e", "b19001_016e", "b19001_017e"],
    },
    "homeownership": {
        "owner": "b25008_002e",
        "renter": "b25008_003e",
    },
    "transportation": {
        "drove_alone": "b08301_003e",
        "carpool": None,  # computed: b08301_002e - b08301_003e
        "transit": "b08301_010e",
        "walking": "b08301_019e",
        "bicycle": "b08301_018e",
        "wfh": "b08301_021e",
        "other": "b08301_020e",
    },
    "age": {
        "under_18": "b01001_003z",
        "18_24": "b01001_004z",
        "25_34": "b01001_005z",
        "35_44": "b01001_006z",
        "45_54": "b01001_007z",
        "55_64": "b01001_008z",
        "65_74": "b01001_009z",
        "75_84": "b01001_010z",
        "85_plus": "b01001_011z",
    },
    "education": {
        "less_than_hs": "b15003_002z",
        "hs_grad": "b15003_003z",
        "some_college": "b15003_004z",
        "associates": "b15003_021e",
        "bachelors": "b15003_022e",
        "masters": "b15003_023e",
        "professional": "b15003_024e",
        "doctorate": "b15003_025e",
    },
}

# All raw columns we need from the database
RAW_COLS = set()
for cat in DEMOGRAPHICS.values():
    for v in cat.values():
        if v is None:
            continue
        if isinstance(v, list):
            RAW_COLS.update(v)
        else:
            RAW_COLS.add(v)
# Add columns for computed fields
RAW_COLS.update(["b02001_001e", "b08301_002e", "b08301_003e", "nwi", "nwi_scaled_10",
                  "state_name", "county_name", "city_name", "csa_name", "geoid10",
                  "geography_type", "b03002_002e"])


def to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def load_block_groups(conn):
    """Load all block group rows with needed columns."""
    cols = ", ".join(sorted(RAW_COLS))
    rows = conn.execute(f"SELECT {cols} FROM nwi_full WHERE geography_type = 'block_group'").fetchall()
    col_names = sorted(RAW_COLS)

    records = []
    for row in rows:
        d = {}
        for i, name in enumerate(col_names):
            val = row[i]
            if name in ("state_name", "county_name", "city_name", "csa_name", "geoid10", "geography_type"):
                d[name] = val
            elif name == "nwi":
                d[name] = int(to_float(val))
            else:
                d[name] = to_float(val)
        # Exclude Puerto Rico — NWI methodology doesn't measure its built environment well
        if d.get("geoid10", "")[:2] == "72":
            continue
        # Compute carpool
        d["_carpool"] = d.get("b08301_002e", 0) - d.get("b08301_003e", 0)
        # Compute Hispanic
        total = d.get("b02001_001e", 0)
        non_hisp = d.get("b03002_002e", 0)
        d["_hispanic"] = (total - non_hisp) if non_hisp > 0 else 0
        records.append(d)

    # Fix state names using FIPS
    state_fips = {
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
    for r in records:
        fips = r.get("geoid10", "")[:2]
        r["state_name"] = state_fips.get(fips, r.get("state_name"))

    return records


def aggregate_demographics(rows):
    """Aggregate demographic columns for a group of block group rows."""
    result = {}
    for cat_name, cols in DEMOGRAPHICS.items():
        cat_result = {}
        for label, col in cols.items():
            if col is None:
                # Carpool — use precomputed
                if label == "carpool":
                    cat_result[label] = sum(r.get("_carpool", 0) for r in rows)
                continue
            if isinstance(col, list):
                cat_result[label] = sum(sum(r.get(c, 0) for c in col) for r in rows)
            else:
                cat_result[label] = sum(r.get(col, 0) for r in rows)
        result[cat_name] = cat_result

    # Add ethnicity
    result["ethnicity"] = {
        "hispanic": sum(r.get("_hispanic", 0) for r in rows),
        "non_hispanic": sum(r.get("b03002_002e", 0) for r in rows),
    }
    return result


def aggregate_jurisdiction(rows):
    """Build a jurisdiction record from its block group rows."""
    total_pop_raw = sum(r["b02001_001e"] for r in rows)

    # Population by NWI level
    by_nwi = {}
    for nwi_level in range(4):
        level_rows = [r for r in rows if r["nwi"] == nwi_level]
        level_pop = sum(r["b02001_001e"] for r in level_rows)
        by_nwi[str(nwi_level)] = {
            "population": round(level_pop),
            "demographics": aggregate_demographics(level_rows),
        }
        # Round demographic values
        for cat in by_nwi[str(nwi_level)]["demographics"].values():
            for k in cat:
                cat[k] = round(cat[k])

    # Total pop = sum of rounded level pops (ensures internal consistency)
    total_pop = sum(by_nwi[str(i)]["population"] for i in range(4))

    # Population-weighted average NWI
    weighted_sum = sum(r["nwi_scaled_10"] * r["b02001_001e"] for r in rows)
    avg_nwi = weighted_sum / total_pop_raw if total_pop_raw > 0 else 0

    return {
        "population": total_pop,
        "avg_nwi": round(avg_nwi, 2),
        "by_nwi": by_nwi,
    }


def _numeric_keys(record):
    """All keys in a BG record that should be area-weighted."""
    skip = {"state_name", "county_name", "city_name", "csa_name", "geoid10",
            "geography_type", "nwi"}
    return [k for k in record if k not in skip and isinstance(record[k], (int, float))]


def aggregate_cities_area_weighted(records):
    """Aggregate BGs to cities using area-weighted interpolation from shapefiles."""
    import geopandas as gpd

    # Index records by geoid10
    bg_data = {r["geoid10"]: r for r in records}

    # State FIPS lookup for naming
    state_names = {}
    for r in records:
        fips = r["geoid10"][:2]
        if fips not in state_names:
            state_names[fips] = r["state_name"]

    # Collect all numeric column keys from a sample record
    sample = records[0]
    num_keys = _numeric_keys(sample)

    # Process state by state
    # city_key -> list of weighted BG dicts
    city_fragments = defaultdict(list)

    bg_dir = BOUNDARIES / "bg_raw"
    places_dir = BOUNDARIES / "places_raw"
    state_fips_list = sorted(state_names.keys())

    for state_fips in state_fips_list:
        bg_shp = list(bg_dir.glob(f"gz_2010_{state_fips}_150_00_500k.shp"))
        pl_shp = list(places_dir.glob(f"tl_2010_{state_fips}_place10.shp"))
        if not bg_shp or not pl_shp:
            continue

        bg_gdf = gpd.read_file(bg_shp[0])
        pl_gdf = gpd.read_file(pl_shp[0])

        # Extract 12-digit FIPS from BG GEO_ID
        bg_gdf["geoid10"] = bg_gdf["GEO_ID"].str.split("US").str[1]

        # Filter to BGs that exist in our data
        bg_gdf = bg_gdf[bg_gdf["geoid10"].isin(bg_data)].copy()
        if bg_gdf.empty:
            continue

        # Project to equal-area CRS for accurate area calculation
        bg_proj = bg_gdf.to_crs("ESRI:102003")
        pl_proj = pl_gdf.to_crs("ESRI:102003")

        # Compute BG total areas before overlay
        bg_proj["bg_area"] = bg_proj.geometry.area

        # Overlay: intersection of BGs and places
        try:
            overlay = gpd.overlay(bg_proj[["geoid10", "bg_area", "geometry"]],
                                  pl_proj[["NAME10", "GEOID10", "geometry"]],
                                  how="intersection")
        except Exception:
            continue

        if overlay.empty:
            continue

        overlay["frac"] = overlay.geometry.area / overlay["bg_area"]

        # Build weighted fragments
        for _, row in overlay.iterrows():
            geoid = row["geoid10"]
            frac = row["frac"]
            place_name = row["NAME10"]
            city_key = f"{place_name}|{state_fips}"

            if geoid not in bg_data or frac < 0.001:
                continue

            bg = bg_data[geoid]
            weighted = {
                "geoid10": geoid,
                "nwi": bg["nwi"],
                "nwi_scaled_10": bg["nwi_scaled_10"],  # rate, not quantity — don't area-weight
                "state_name": bg["state_name"],
            }
            for k in num_keys:
                if k == "nwi_scaled_10":
                    continue  # already set above, unweighted
                weighted[k] = bg[k] * frac
            # Also weight computed fields
            weighted["_carpool"] = bg.get("_carpool", 0) * frac
            weighted["_hispanic"] = bg.get("_hispanic", 0) * frac

            city_fragments[city_key].append(weighted)

        state_name = state_names[state_fips]
        n_places = len({k for k in city_fragments if k.endswith(f"|{state_fips}")})
        print(f"  {state_fips} ({state_name}): {n_places} places")

    # Aggregate fragments into jurisdiction records
    result = {}
    for city_key, fragments in city_fragments.items():
        parts = city_key.rsplit("|", 1)
        place_name, state_fips = parts
        state_name = state_names.get(state_fips, state_fips)
        entry = aggregate_jurisdiction(fragments)
        if entry["population"] == 0:
            continue
        entry["name"] = f"{place_name}, {state_name}"
        result[city_key] = entry

    return result


def build_level(records, key_fn, name_fn):
    """Group records by jurisdiction and aggregate."""
    groups = {}
    for r in records:
        key = key_fn(r)
        if key is None:
            continue
        groups.setdefault(key, []).append(r)

    result = {}
    for key, rows in groups.items():
        name = name_fn(rows[0])
        if name is None:
            continue
        entry = aggregate_jurisdiction(rows)
        entry["name"] = name
        result[key] = entry

    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    print("Loading block groups...")
    conn = sqlite3.connect(str(DB))
    records = load_block_groups(conn)
    conn.close()
    print(f"  Loaded {len(records):,} block groups")

    # States — key by FIPS
    print("Aggregating states...")
    states = build_level(
        records,
        key_fn=lambda r: r["geoid10"][:2],
        name_fn=lambda r: r["state_name"],
    )
    with open(OUT / "states.json", "w") as f:
        json.dump(states, f, separators=(",", ":"))
    print(f"  {len(states)} states")

    # Counties — key by 5-digit FIPS (the 2010 county; names are the 2010 SF1's, from the database)
    print("Aggregating counties...")
    counties = build_level(
        records,
        key_fn=lambda r: r["geoid10"][:5],
        name_fn=lambda r: r["county_name"],
    )

    with open(OUT / "counties.json", "w") as f:
        json.dump(counties, f, separators=(",", ":"))
    print(f"  {len(counties)} counties")

    # Cities — area-weighted block group interpolation
    print("Aggregating cities (area-weighted)...")
    cities = aggregate_cities_area_weighted(records)
    with open(OUT / "cities.json", "w") as f:
        json.dump(cities, f, separators=(",", ":"))
    print(f"  {len(cities)} cities")

    # CSAs — key by csa_name
    print("Aggregating CSAs...")
    csas = build_level(
        records,
        key_fn=lambda r: r.get("csa_name"),
        name_fn=lambda r: r.get("csa_name"),
    )
    with open(OUT / "csas.json", "w") as f:
        json.dump(csas, f, separators=(",", ":"))
    print(f"  {len(csas)} CSAs")

    # National
    print("Aggregating national...")
    national = aggregate_jurisdiction(records)
    national["name"] = "United States"
    with open(OUT / "national.json", "w") as f:
        json.dump(national, f, separators=(",", ":"))

    print("\nDone. Files in public/data/")


if __name__ == "__main__":
    main()
