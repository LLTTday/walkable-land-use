#!/usr/bin/env python3
"""
Place-by-county walkability.

A Census place that spans several counties is reported once per county it
falls in, with that piece's own population and population-weighted
walkability. Block groups do not nest in places, so a block group cut by a
place boundary is apportioned between the pieces.

Nothing is measured at the block. The NWI score, the categorical level, and
every ACS count are block group attributes. A share table decides what
fraction of a block group belongs to each place; each (block group × place)
piece inherits the whole block group's walkability and its ACS counts scaled
by the share. This module builds the share table from 2010 block population
(blocks nest in 2010 places) and aggregates it. The area session builds a
different share table and reuses `aggregate()`.

    uv run python v2/scripts/place_county.py build block      # block-population shares → CSV + verification report
    uv run python v2/scripts/place_county.py build area       # area shares → CSV + report + side-by-side with block
    uv run python v2/scripts/place_county.py nesting 10 08    # geometric nesting check for DE and CO
    uv run python v2/scripts/place_county.py slivers 10 08    # full-resolution vs cartographic overlay, no threshold
    uv run python v2/scripts/place_county.py compare          # side-by-side of the two products (from their CSVs)
    uv run python v2/scripts/place_county.py weights          # the two share tables as products, with a round-trip check

Inputs (see download_blocks.sh):
    boundaries/baf/BlockAssign_ST{FIPS}_{ST}_INCPLACE_CDP.txt   block → PLACEFP
    boundaries/blocks_pophu/tabblock2010_{FIPS}_pophu.shp       block POP10 / HOUSING10 (no PR file)
    boundaries/blocks_pophu/pr*.pl                              PL 94-171 for Puerto Rico
    boundaries/bg_full/tl_2010_{FIPS}_bg10.shp                  full-resolution 2010 block groups (area method)
    boundaries/places_raw/tl_2010_{FIPS}_place10.dbf            place names and class
    boundaries/counties/gz_2010_us_050_00_500k.dbf              county names
    nwi_analysis/data/nwi_full_2019_complete.db                 block group NWI + ACS
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "nwi_analysis" / "data" / "nwi_full_2019_complete.db"
OUT_DIR = ROOT / "nwi_analysis" / "data"
BOUNDARIES = Path(__file__).resolve().parent / "boundaries"
BAF_DIR = BOUNDARIES / "baf"
POPHU_DIR = BOUNDARIES / "blocks_pophu"
PLACES_DIR = BOUNDARIES / "places_raw"
BG_FULL_DIR = BOUNDARIES / "bg_full"
BG_CARTO_DIR = BOUNDARIES / "bg_raw"
COUNTIES_DBF = BOUNDARIES / "counties" / "gz_2010_us_050_00_500k.dbf"
CITIES_JSON = Path(__file__).resolve().parent.parent / "public" / "data" / "cities.json"

sys.path.insert(0, str(ROOT / "nwi_analysis"))
from config import field_dict  # noqa: E402  — the ACS column names; never guessed

# FIPS → USPS abbreviation, as it appears in the Census BAF filenames.
STATES = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "72": "PR",
}
STATE_NAMES = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas", "06": "California",
    "08": "Colorado", "09": "Connecticut", "10": "Delaware", "11": "District of Columbia",
    "12": "Florida", "13": "Georgia", "15": "Hawaii", "16": "Idaho", "17": "Illinois",
    "18": "Indiana", "19": "Iowa", "20": "Kansas", "21": "Kentucky", "22": "Louisiana",
    "23": "Maine", "24": "Maryland", "25": "Massachusetts", "26": "Michigan", "27": "Minnesota",
    "28": "Mississippi", "29": "Missouri", "30": "Montana", "31": "Nebraska", "32": "Nevada",
    "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico", "36": "New York",
    "37": "North Carolina", "38": "North Dakota", "39": "Ohio", "40": "Oklahoma",
    "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island", "45": "South Carolina",
    "46": "South Dakota", "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont",
    "51": "Virginia", "53": "Washington", "54": "West Virginia", "55": "Wisconsin",
    "56": "Wyoming", "72": "Puerto Rico",
}
# The cartographic county file abbreviates its legal/statistical area descriptions.
COUNTY_LSAD = {
    "County": "County", "Parish": "Parish", "city": "city", "Borough": "Borough",
    "CA": "Census Area", "Cty&Bor": "City and Borough", "Muno": "Municipio",
    "Muny": "Municipality",
}

NWI_LEVEL_COLUMNS = ["1 - Least Walkable", "2 - Below Avg", "3 - Above Avg", "4 - Most Walkable"]
OUTPUT_COLUMNS = [
    "Place FIPS", "Place Name", "Class", "State", "County FIPS", "County Name",
    "Share of Place Population", "Raw NWI", "Scaled NWI", "Population",
] + NWI_LEVEL_COLUMNS

# ACS count columns, from config.py. B03002_012E (Hispanic) is computed downstream, not stored.
ACS_COUNT_COLUMNS = sorted({
    col.lower()
    for group in field_dict.values()
    for col in group.values()
    if col != "B03002_012E"
})
RATE_COLUMNS = ["nwi", "natwalkind", "nwi_scaled_10"]

SHARE_TOLERANCE = 1e-9

# Equal-area projections for area ratios. CONUS: Albers (ESRI:102003). Alaska Albers, Hawaii Albers.
# Puerto Rico has no standard equal-area CRS; a local Albers centred on the island is used and
# recorded in the methodology. Ratios within a block group are insensitive to the exact choice.
CONUS_EQUAL_AREA = "ESRI:102003"
EQUAL_AREA_CRS = {
    "02": "EPSG:3338",
    "15": "ESRI:102007",
    "72": "+proj=aea +lat_1=17.9 +lat_2=18.5 +lat_0=18.2 +lon_0=-66.4 +datum=NAD83 +units=m +no_defs",
}
NOISE_FLOOR_M2 = 1.0  # intersection pieces smaller than this are floating-point residue, not land


def equal_area_crs(fips):
    return EQUAL_AREA_CRS.get(fips, CONUS_EQUAL_AREA)


# ----------------------------------------------------------------------------
# Block group attributes, place names, county names
# ----------------------------------------------------------------------------

def load_block_groups():
    """Block group rows from the NWI database, numeric, indexed by 12-digit GEOID."""
    import sqlite3

    cols = ["geoid10", "county_name"] + RATE_COLUMNS + ACS_COUNT_COLUMNS
    with sqlite3.connect(str(DB)) as conn:
        df = pd.read_sql(
            f"SELECT {', '.join(cols)} FROM nwi_full WHERE geography_type = 'block_group'", conn
        )
    for c in RATE_COLUMNS + ACS_COUNT_COLUMNS:
        df[c] = pd.to_numeric(df[c], errors="coerce")  # some ACS columns are stored as TEXT
    df["nwi"] = df["nwi"].astype(int)
    assert df["geoid10"].str.len().eq(12).all(), "block group GEOIDs must be 12 digits"
    assert df["geoid10"].is_unique
    return df.set_index("geoid10")


def load_places():
    """All 2010 places: 7-digit GEOID → Census NAME10 and CLASSFP10. Attribute tables only."""
    import geopandas as gpd

    frames = []
    for fips in STATES:
        dbf = PLACES_DIR / f"tl_2010_{fips}_place10.dbf"
        if not dbf.exists():
            raise FileNotFoundError(dbf)
        p = gpd.read_file(dbf, read_geometry=False)[["GEOID10", "NAME10", "CLASSFP10"]]
        frames.append(p)
    places = pd.concat(frames, ignore_index=True)
    assert places["GEOID10"].is_unique
    return places.rename(
        columns={"GEOID10": "place_geoid", "NAME10": "place_name", "CLASSFP10": "class"}
    ).set_index("place_geoid")


def load_county_names(block_groups):
    """5-digit county FIPS → name. The database's county_name where it has one (it is what the
    other exports use); the county shapefile's NAME + LSAD + state otherwise. The two sources are
    compared where both exist and the disagreements returned for the report."""
    import geopandas as gpd

    shp = gpd.read_file(COUNTIES_DBF, read_geometry=False)
    shp["fips"] = shp["STATE"] + shp["COUNTY"]
    lsad = shp["LSAD"].map(COUNTY_LSAD)
    unknown = shp.loc[shp["LSAD"].notna() & lsad.isna(), "LSAD"].unique()
    assert len(unknown) == 0, f"unmapped county LSAD codes: {unknown}"
    shp["name"] = (
        shp["NAME"] + lsad.map(lambda s: f" {s}" if isinstance(s, str) else "")
        + ", " + shp["STATE"].map(STATE_NAMES).fillna("")
    ).str.strip(", ")
    from_shapefile = shp.set_index("fips")["name"].to_dict()

    db_names = block_groups["county_name"].dropna()
    db_names = db_names[db_names.str.len() > 0]
    from_db = db_names.groupby(db_names.index.str[:5]).agg(lambda s: s.mode().iloc[0]).to_dict()

    disagreements = {
        f: (from_db[f], from_shapefile[f])
        for f in from_db
        if f in from_shapefile and from_db[f] != from_shapefile[f]
    }
    names = dict(from_shapefile)
    names.update(from_db)
    return names, disagreements, len(from_db), len(from_shapefile)


# ----------------------------------------------------------------------------
# Block inputs
# ----------------------------------------------------------------------------

def read_baf(fips):
    """Block → PLACEFP for one state. Empty PLACEFP = the block is outside every place."""
    path = BAF_DIR / f"BlockAssign_ST{fips}_{STATES[fips]}_INCPLACE_CDP.txt"
    baf = pd.read_csv(path, dtype=str, keep_default_na=False)
    baf.columns = [c.strip() for c in baf.columns]
    baf["BLOCKID"] = baf["BLOCKID"].str.strip()
    baf["PLACEFP"] = baf["PLACEFP"].str.strip()
    assert list(baf.columns) == ["BLOCKID", "PLACEFP"], baf.columns
    assert baf["BLOCKID"].str.len().eq(15).all()
    assert baf["BLOCKID"].is_unique
    assert baf["PLACEFP"].str.len().isin([0, 5]).all()
    return baf


def read_block_population(fips):
    """Block → POP10, HOUSING10 for one state, from the pophu attribute table (or PL 94-171 for PR)."""
    if fips == "72":
        return read_pr_pl94171()
    import geopandas as gpd

    dbf = POPHU_DIR / f"tabblock2010_{fips}_pophu.dbf"
    blocks = gpd.read_file(dbf, read_geometry=False)[["BLOCKID10", "POP10", "HOUSING10"]]
    blocks = blocks.rename(columns={"BLOCKID10": "BLOCKID"})
    blocks["POP10"] = blocks["POP10"].astype(int)
    blocks["HOUSING10"] = blocks["HOUSING10"].astype(int)
    assert blocks["BLOCKID"].is_unique
    return blocks


# 2010 PL 94-171 geographic header: fixed-width, 1-indexed positions from the technical documentation.
# Used only for Puerto Rico, which has no TIGER pophu file. The parse is verified by matching the
# resulting block set against the PR Block Assignment File and the state total against SUMLEV 040.
PL_GEO = {"SUMLEV": (9, 11), "LOGRECNO": (19, 25), "STATE": (28, 29), "COUNTY": (30, 32),
          "TRACT": (55, 60), "BLOCK": (62, 65)}
PR_2010_POPULATION = 3_725_789  # 2010 Census, Puerto Rico


def read_pr_pl94171():
    geo_path = POPHU_DIR / "prgeo2010.pl"
    rows = []
    with open(geo_path, encoding="latin-1") as f:
        for line in f:
            rec = {k: line[a - 1:b].strip() for k, (a, b) in PL_GEO.items()}
            if rec["SUMLEV"] in ("040", "750"):
                rows.append(rec)
    geo = pd.DataFrame(rows)
    geo["LOGRECNO"] = geo["LOGRECNO"].astype(int)

    # Segment layout from the packing list shipped in the zip (pr2010.pl.prd.packinglist.txt):
    # five header fields (FILEID,STUSAB,CHARITER,CIFSN,LOGRECNO), then file 1 = P1 (71 cells) + P2 (73),
    # file 2 = P3 (71) + P4 (73) + H1 (3). P0010001 is file 1 cell 6; H0010001..3 are file 2 cells 150..152.
    f1 = pd.read_csv(POPHU_DIR / "pr000012010.pl", header=None, usecols=[4, 5], names=["LOGRECNO", "POP10"])
    f2 = pd.read_csv(POPHU_DIR / "pr000022010.pl", header=None, usecols=[4, 149, 150, 151],
                     names=["LOGRECNO", "HOUSING10", "H_OCC", "H_VAC"])
    assert (f2["HOUSING10"] == f2["H_OCC"] + f2["H_VAC"]).all(), "H0010001 must equal occupied + vacant"
    geo = geo.merge(f1, on="LOGRECNO").merge(f2[["LOGRECNO", "HOUSING10"]], on="LOGRECNO")

    state = geo[geo["SUMLEV"] == "040"]
    assert len(state) == 1 and int(state["POP10"].iloc[0]) == PR_2010_POPULATION, state
    blocks = geo[geo["SUMLEV"] == "750"].copy()
    blocks["BLOCKID"] = blocks["STATE"] + blocks["COUNTY"] + blocks["TRACT"] + blocks["BLOCK"]
    assert blocks["BLOCKID"].str.len().eq(15).all()
    assert blocks["BLOCKID"].is_unique
    assert int(blocks["POP10"].sum()) == PR_2010_POPULATION, blocks["POP10"].sum()
    return blocks[["BLOCKID", "POP10", "HOUSING10"]].reset_index(drop=True)


# ----------------------------------------------------------------------------
# Share table — block population
# ----------------------------------------------------------------------------

def block_share_table(fips):
    """(bg_geoid, place_geoid, share, share_basis) for one state, from 2010 block population.

    share = Σ POP10 of the block group's blocks inside the place / Σ POP10 of all its blocks.
    Blocks outside every place sit in the denominator and produce no row. A block group with no
    2010 population falls back to housing units, then to an equal split by block count.
    """
    baf = read_baf(fips)
    pop = read_block_population(fips)
    blocks = baf.merge(pop, on="BLOCKID", how="outer", indicator=True)
    unmatched = blocks["_merge"].value_counts()
    assert unmatched.get("left_only", 0) == 0 and unmatched.get("right_only", 0) == 0, (
        f"{fips}: BAF and block population disagree on the block set: {unmatched.to_dict()}"
    )
    blocks = blocks.drop(columns="_merge")
    blocks["bg_geoid"] = blocks["BLOCKID"].str[:12]

    totals = blocks.groupby("bg_geoid").agg(pop=("POP10", "sum"), housing=("HOUSING10", "sum"), n=("BLOCKID", "size"))
    basis = pd.Series("pop", index=totals.index)
    basis[totals["pop"] == 0] = "housing"
    basis[(totals["pop"] == 0) & (totals["housing"] == 0)] = "blocks"
    blocks = blocks.join(basis.rename("share_basis"), on="bg_geoid")
    weight = blocks["POP10"].astype(float)
    weight[blocks["share_basis"] == "housing"] = blocks.loc[blocks["share_basis"] == "housing", "HOUSING10"]
    weight[blocks["share_basis"] == "blocks"] = 1.0
    blocks["weight"] = weight
    denominator = blocks.groupby("bg_geoid")["weight"].transform("sum")
    blocks["share"] = blocks["weight"] / denominator

    inside = blocks[blocks["PLACEFP"] != ""].copy()
    inside["place_geoid"] = fips + inside["PLACEFP"]
    shares = (
        inside.groupby(["bg_geoid", "place_geoid", "share_basis"], as_index=False)["share"].sum()
    )
    # 2010 block population per (bg, place) rides along for the spot checks: it is the decennial
    # place-in-county count when summed, independent of the ACS.
    pop2010 = inside.groupby(["bg_geoid", "place_geoid"])["POP10"].sum().rename("pop2010")
    shares = shares.join(pop2010, on=["bg_geoid", "place_geoid"])
    return shares[["bg_geoid", "place_geoid", "share", "share_basis", "pop2010"]], basis



# ----------------------------------------------------------------------------
# Share table — area
# ----------------------------------------------------------------------------

def read_block_group_geometry(fips, source="full"):
    """Block group polygons for one state with a 12-digit `bg_geoid`.

    "full": TIGER 2010 tl_2010_XX_bg10 (the product). "carto": the 500k cartographic files, used
    only by the sliver comparison — their generalized edges do not coincide with TIGER places.
    """
    import geopandas as gpd

    if source == "full":
        bg = gpd.read_file(BG_FULL_DIR / f"tl_2010_{fips}_bg10.shp")[["GEOID10", "geometry"]]
        bg = bg.rename(columns={"GEOID10": "bg_geoid"})
    elif source == "carto":
        bg = gpd.read_file(BG_CARTO_DIR / f"gz_2010_{fips}_150_00_500k.shp")[["GEO_ID", "geometry"]]
        bg["bg_geoid"] = bg["GEO_ID"].str.split("US").str[1]
        bg = bg[["bg_geoid", "geometry"]]
    else:
        raise ValueError(source)
    assert bg["bg_geoid"].str.len().eq(12).all()
    assert bg["bg_geoid"].is_unique, f"{fips}: duplicate block group IDs in the {source} file"
    return bg


def area_share_table(fips, source="full", noise_floor=NOISE_FLOOR_M2):
    """(bg_geoid, place_geoid, share, share_basis="area") for one state, by overlay.

    share = area of (block group ∩ place) / area of the block group, both in an equal-area
    projection. Pieces smaller than `noise_floor` square metres are dropped as numerical residue
    and counted. No fraction threshold. Returns the share table and a dict of overlay statistics.
    """
    import geopandas as gpd
    from shapely import make_valid

    crs = equal_area_crs(fips)
    bg = read_block_group_geometry(fips, source).to_crs(crs)
    pl = gpd.read_file(PLACES_DIR / f"tl_2010_{fips}_place10.shp")[["GEOID10", "geometry"]].to_crs(crs)
    pl = pl.rename(columns={"GEOID10": "place_geoid"})

    stats = {"fips": fips, "crs": crs, "block_groups": len(bg), "places": len(pl)}
    stats["invalid_bg"] = int((~bg.is_valid).sum())
    stats["invalid_places"] = int((~pl.is_valid).sum())
    if stats["invalid_bg"]:
        bg["geometry"] = bg.geometry.apply(make_valid)
    if stats["invalid_places"]:
        pl["geometry"] = pl.geometry.apply(make_valid)

    # Places should not overlap one another in TIGER 2010; measure it rather than assume it.
    place_area_sum = pl.geometry.area.sum()
    stats["place_overlap_m2"] = float(place_area_sum - pl.geometry.union_all().area)

    bg["bg_area"] = bg.geometry.area
    inter = gpd.overlay(bg, pl, how="intersection", keep_geom_type=True)
    inter["piece_area"] = inter.geometry.area
    stats["pieces"] = len(inter)
    stats["pieces_under_1m2"] = int((inter["piece_area"] < 1.0).sum())
    tiny = inter["piece_area"] < noise_floor
    stats["pieces_dropped"] = int(tiny.sum())
    stats["area_dropped_m2"] = float(inter.loc[tiny, "piece_area"].sum())
    inter = inter[~tiny]
    inter["share"] = inter["piece_area"] / inter["bg_area"]
    stats["pieces_under_10m2"] = int((inter["piece_area"] < 10).sum())
    stats["pieces_under_0.1pct"] = int((inter["share"] < 0.001).sum())
    stats["pieces_under_1pct"] = int((inter["share"] < 0.01).sum())

    shares = inter.groupby(["bg_geoid", "place_geoid"], as_index=False)["share"].sum()
    shares["share_basis"] = "area"
    return shares[["bg_geoid", "place_geoid", "share", "share_basis"]], stats


def sliver_report(fips):
    """The same overlay on full-resolution and on cartographic block groups, with no noise floor,
    so the piece-size distributions can be compared. This is the evidence that full-resolution
    geometry removes the need for a fraction threshold."""
    print(f"\n== Sliver comparison {STATES[fips]} — pieces by size, no noise floor ==")
    print(f"  {'geometry':<16}{'pieces':>9}{'< 1 m²':>9}{'< 10 m²':>9}{'< 0.1%':>9}{'< 1%':>9}{'invalid bg':>12}")
    for source in ("full", "carto"):
        _, st = area_share_table(fips, source=source, noise_floor=0.0)
        print(f"  {source:<16}{st['pieces']:>9,}{st['pieces_under_1m2']:>9,}{st['pieces_under_10m2']:>9,}"
              f"{st['pieces_under_0.1pct']:>9,}{st['pieces_under_1pct']:>9,}{st['invalid_bg']:>12,}")

# ----------------------------------------------------------------------------
# Aggregation — shared by both share tables
# ----------------------------------------------------------------------------

def apportion(shares, block_groups):
    """One row per (bg, place) piece: ACS counts scaled by share, rates carried whole."""
    pieces = shares.join(block_groups, on="bg_geoid", how="inner")
    for c in ["b02001_001e"] + [c for c in ACS_COUNT_COLUMNS if c != "b02001_001e"]:
        pieces[c] = pieces[c] * pieces["share"]
    pieces["county_fips"] = pieces["bg_geoid"].str[:5]
    return pieces


def aggregate(shares, block_groups, places, county_names):
    """Group apportioned pieces by (place, county) into the output table.

    Same arithmetic as generate_export_summaries.py — population by level is a sum, the two NWI
    figures are population-weighted means — only the weights are apportioned. A piece with no
    ACS population gets no NWI (empty), not zero.
    """
    pieces = apportion(shares, block_groups)
    pop = pieces["b02001_001e"].fillna(0.0)
    pieces["_pop"] = pop
    pieces["_raw_x_pop"] = pieces["natwalkind"] * pop
    pieces["_scaled_x_pop"] = pieces["nwi_scaled_10"] * pop
    for level, col in enumerate(NWI_LEVEL_COLUMNS):
        pieces[col] = pop.where(pieces["nwi"] == level, 0.0)

    g = pieces.groupby(["place_geoid", "county_fips"])
    out = g[["_pop", "_raw_x_pop", "_scaled_x_pop"] + NWI_LEVEL_COLUMNS].sum()
    out = out.rename(columns={"_pop": "Population"})
    has_pop = out["Population"] > 0
    out["Raw NWI"] = (out["_raw_x_pop"] / out["Population"]).where(has_pop)
    out["Scaled NWI"] = (out["_scaled_x_pop"] / out["Population"]).where(has_pop)
    place_total = out.groupby(level="place_geoid")["Population"].transform("sum")
    out["Share of Place Population"] = (out["Population"] / place_total).where(place_total > 0)
    out = out.reset_index()

    out["Place FIPS"] = out["place_geoid"]
    out["Place Name"] = out["place_geoid"].map(places["place_name"])
    out["Class"] = out["place_geoid"].map(places["class"])
    out["State"] = out["place_geoid"].str[:2].map(STATE_NAMES)
    out["County FIPS"] = out["county_fips"]
    out["County Name"] = out["county_fips"].map(county_names)
    missing_place = out["Place Name"].isna().sum()
    assert missing_place == 0, f"{missing_place} rows have a place GEOID with no name in the place shapefiles"
    out = out[OUTPUT_COLUMNS].sort_values(["Place FIPS", "County FIPS"]).reset_index(drop=True)
    return out, pieces


def write_output(out, path):
    formatted = out.copy()
    for c in ["Population"] + NWI_LEVEL_COLUMNS:
        formatted[c] = formatted[c].round().astype(int)
    formatted.to_csv(path, index=False)


# ----------------------------------------------------------------------------
# Verification
# ----------------------------------------------------------------------------

def verify(shares, out, pieces, block_groups, basis_by_bg=None):
    """Reconciliations and share invariants. Prints a report; returns True if every invariant holds.

    basis_by_bg: share_basis per block group, including block groups that produced no share row.
    When omitted (the area session), it is read from the share table.
    """
    ok = True
    if basis_by_bg is None:
        basis_by_bg = shares.drop_duplicates("bg_geoid").set_index("bg_geoid")["share_basis"]
    print("\n== Share invariants ==")
    sums = shares.groupby("bg_geoid")["share"].sum()
    over = sums[sums > 1 + SHARE_TOLERANCE]
    print(f"block groups with a share row: {len(sums):,}  max Σshare = {sums.max():.12f}  over 1: {len(over)}")
    if len(over):
        ok = False
        print(over.head(10))
    whole = (sums > 1 - SHARE_TOLERANCE).sum()
    print(f"  entirely inside places: {whole:,}   partially: {len(sums) - whole:,}")

    print("\n== Fallback basis (block groups with no 2010 block population) ==")
    bg_pop = block_groups["b02001_001e"].fillna(0)
    for b in ["pop", "housing", "blocks"]:
        ids = basis_by_bg.index[basis_by_bg == b]
        in_db = ids.intersection(block_groups.index)
        print(f"  {b:8s} {len(ids):>8,} block groups   {len(in_db):>8,} in the NWI data   "
              f"ACS population carried: {bg_pop.reindex(in_db).sum():,.0f}")

    print("\n== State reconciliation ==")
    print("  apportioned into places + remainder outside places == Σ ACS population of the state's block groups")
    bgs_with_share = shares.groupby("bg_geoid")["share"].sum()
    remainder_share = (1 - bgs_with_share).reindex(block_groups.index).fillna(1.0)
    remainder_pop = (remainder_share * bg_pop)
    state_of = block_groups.index.str[:2]
    apportioned_by_state = pieces.groupby(pieces["bg_geoid"].str[:2])["_pop"].sum()
    worst = 0.0
    for st in sorted(STATES):
        total = bg_pop[state_of == st].sum()
        rem = remainder_pop[state_of == st].sum()
        app = apportioned_by_state.get(st, 0.0)
        diff = abs(app + rem - total)
        worst = max(worst, diff)
        if diff > 1e-6 * max(total, 1):
            ok = False
            print(f"  {st} MISMATCH: apportioned {app:,.2f} + remainder {rem:,.2f} != total {total:,.0f}")
    print(f"  {len(STATES)} states checked, worst absolute difference {worst:.3e}")
    not_covered = sorted(set(state_of) - set(STATES))
    if not_covered:
        n = state_of.isin(not_covered).sum()
        print(f"  block groups in {not_covered} ({n:,}) have no place inputs and are outside the product")

    print("\n== Place reconciliation ==")
    place_sum = out.groupby("Place FIPS")["Share of Place Population"].sum()
    with_pop = out.groupby("Place FIPS")["Population"].sum() > 0
    bad = place_sum[with_pop & ~np.isclose(place_sum, 1.0, atol=1e-9)]
    print(f"  places: {place_sum.size:,}   rows: {len(out):,}   multi-county places: "
          f"{(out.groupby('Place FIPS').size() > 1).sum():,}   Σ share != 1: {len(bad)}")
    if len(bad):
        ok = False
        print(bad.head(10))
    zero_pop = (out["Population"] == 0).sum()
    print(f"  rows with no ACS population (NWI left empty): {zero_pop}")

    print("\n== Block groups in the share table but not in the NWI data ==")
    missing = set(shares["bg_geoid"]) - set(block_groups.index)
    print(f"  {len(missing):,} (they have no NWI score or ACS row; nothing to apportion)")
    return ok


KNOWN_MULTI_COUNTY = {
    # place GEOID: (name, 2010 Census population of the whole place)
    "0804000": ("Aurora, CO", 325_078),
    "2938000": ("Kansas City, MO", 459_787),
    "4055000": ("Oklahoma City, OK", 579_999),
    "3651000": ("New York, NY", 8_175_133),
    "3918000": ("Columbus, OH", 787_033),
}
NYC_BOROUGHS_2010 = {"36005": 1_385_108, "36047": 2_504_700, "36061": 1_585_873, "36081": 2_230_722, "36085": 468_730}


def spot_check_multi_county(out, pieces):
    print("\n== Known multi-county cities ==")
    has_2010 = "pop2010" in pieces.columns  # only the block share table carries decennial block counts
    if has_2010:
        print("  Population is ACS 2015–2019 apportioned; 2010 is the decennial count of the blocks in that piece.")
        pop2010 = pieces.groupby(["place_geoid", "county_fips"])["pop2010"].sum()
    else:
        pop2010 = pd.Series(dtype=float)
    for geoid, (label, total_2010) in KNOWN_MULTI_COUNTY.items():
        rows = out[out["Place FIPS"] == geoid]
        if rows.empty:
            print(f"  {label}: NOT IN OUTPUT")
            continue
        if has_2010:
            summed = sum(pop2010.get((geoid, cf), 0) for cf in rows["County FIPS"])
            flag = "ok" if summed == total_2010 else f"MISMATCH (expected {total_2010:,})"
            print(f"  {label} — {len(rows)} counties; 2010 blocks sum to {summed:,} [{flag}]")
        else:
            print(f"  {label} — {len(rows)} counties (2010 Census total {total_2010:,})")
        for _, r in rows.iterrows():
            b2010 = pop2010.get((geoid, r["County FIPS"]), 0)
            extra = ""
            if has_2010 and r["County FIPS"] in NYC_BOROUGHS_2010:
                extra = "  ok" if b2010 == NYC_BOROUGHS_2010[r["County FIPS"]] else "  MISMATCH vs borough 2010"
            tail = f"  2010 {b2010:>10,}{extra}" if has_2010 else ""
            print(f"      {r['County FIPS']} {r['County Name']:<40} pop {r['Population']:>12,.0f}  "
                  f"share {r['Share of Place Population']:.4f}  scaled NWI {r['Scaled NWI']:.2f}{tail}")


def compare_single_county(out, places):
    """Places wholly in one county vs the v2 cities.json (area-weighted, cartographic block groups).

    cities.json is keyed by NAME10|state, so any name shared by two places in a state (a city and a
    CDP, two Centervilles) was merged there; those names are excluded from the comparison.
    """
    import json

    print("\n== Single-county places vs v2 cities.json ==")
    if not CITIES_JSON.exists():
        print(f"  {CITIES_JSON} not found; skipped")
        return
    cities = json.load(open(CITIES_JSON))
    v2 = pd.DataFrame(
        [{"key": k, "v2_pop": v["population"], "v2_nwi": v["avg_nwi"]} for k, v in cities.items()]
    ).set_index("key")
    all_keys = places["place_name"] + "|" + places.index.str[:2]
    collided = set(all_keys[all_keys.duplicated(keep=False)])
    counts = out.groupby("Place FIPS").size()
    single = out[out["Place FIPS"].map(counts) == 1].copy()
    single["key"] = single["Place Name"] + "|" + single["Place FIPS"].str[:2]
    single = single[~single["key"].isin(collided)]
    j = single.join(v2, on="key", how="inner")
    j = j[j["v2_pop"] > 0]
    j["diff"] = j["Population"] - j["v2_pop"]
    j["rel"] = j["diff"] / j["v2_pop"]
    print(f"  single-county places: {int((counts == 1).sum()):,}   compared: {len(j):,}   "
          f"(names shared by two places in a state, skipped: {len(collided):,})")
    print("  relative difference (block − area) / area, by 2015–2019 population of the place:")
    bands = [(0, 1_000), (1_000, 10_000), (10_000, 100_000), (100_000, np.inf)]
    for lo, hi in bands:
        b = j[(j["Population"] >= lo) & (j["Population"] < hi)]
        if b.empty:
            continue
        label = f"{lo:,}–{hi:,.0f}" if np.isfinite(hi) else f"{lo:,}+"
        print(f"      {label:<18} n {len(b):>6,}   median {b['rel'].median():+.1%}   "
              f"median |rel| {b['rel'].abs().median():.1%}   within 5%: {(b['rel'].abs() < 0.05).mean():.0%}   "
              f"within 25%: {(b['rel'].abs() < 0.25).mean():.0%}")
    print("  largest absolute differences:")
    for _, r in j.reindex(j["diff"].abs().sort_values(ascending=False).index).head(12).iterrows():
        print(f"      {r['Place Name'] + ', ' + r['State']:<32} block {r['Population']:>10,.0f}   "
              f"area {r['v2_pop']:>10,}   diff {r['diff']:>+10,.0f} ({r['rel']:+.1%})   "
              f"scaled NWI {r['Scaled NWI']:.2f} vs {r['v2_nwi']:.2f}")



# ----------------------------------------------------------------------------
# Side-by-side — the two products
# ----------------------------------------------------------------------------

def compare_products(block_shares=None, area_shares=None, block_groups=None):
    """Join the block and area products on (Place FIPS, County FIPS) and report the differences.
    With the share tables, the 25 largest disagreements each get the block group that drives them."""
    key = ["Place FIPS", "County FIPS"]
    dt = {"Place FIPS": str, "County FIPS": str}
    b = pd.read_csv(OUT_DIR / "export_places_by_county_block.csv", dtype=dt).set_index(key)
    a = pd.read_csv(OUT_DIR / "export_places_by_county_area.csv", dtype=dt).set_index(key)
    j = b.merge(a, how="outer", left_index=True, right_index=True, suffixes=("_block", "_area"), indicator=True)

    print("\n== Block vs area products ==")
    only_b = j[j["_merge"] == "left_only"]
    only_a = j[j["_merge"] == "right_only"]
    print(f"  rows: block {len(b):,}   area {len(a):,}   in both {int((j['_merge'] == 'both').sum()):,}")
    print(f"  only in block: {len(only_b):,} (pop {only_b['Population_block'].sum():,.0f})   "
          f"only in area: {len(only_a):,} (pop {only_a['Population_area'].sum():,.0f})")
    both = j[j["_merge"] == "both"].copy()
    both["diff"] = both["Population_block"] - both["Population_area"]
    both["rel"] = both["diff"] / both["Population_area"].where(both["Population_area"] > 0)
    d = both["diff"]
    print(f"  population difference (block − area): identical {int((d == 0).sum()):,}; "
          f"|diff| ≤ 10: {(d.abs() <= 10).mean():.1%}; ≤ 100: {(d.abs() <= 100).mean():.1%}; "
          f"≤ 1,000: {(d.abs() <= 1000).mean():.1%}; > 10,000: {int((d.abs() > 10000).sum()):,}")
    r = both["rel"].dropna()
    print(f"  relative: median {r.median():+.2%}   |rel| median {r.abs().median():.2%}   "
          f"90th pct {r.abs().quantile(0.9):.1%}   within 5%: {(r.abs() < 0.05).mean():.1%}")
    nwi = (both["Scaled NWI_block"] - both["Scaled NWI_area"]).dropna()
    print(f"  scaled NWI difference: |Δ| median {nwi.abs().median():.3f}   90th pct {nwi.abs().quantile(0.9):.3f}   "
          f"max {nwi.abs().max():.2f}")
    print("  by 2015–2019 population of the row (area product):")
    for lo, hi in [(0, 1_000), (1_000, 10_000), (10_000, 100_000), (100_000, np.inf)]:
        band = both[(both["Population_area"] >= lo) & (both["Population_area"] < hi)]
        if band.empty:
            continue
        label = f"{lo:,}–{hi:,.0f}" if np.isfinite(hi) else f"{lo:,}+"
        rr = band["rel"].dropna()
        print(f"      {label:<18} n {len(band):>6,}   median {rr.median():+.1%}   |rel| median {rr.abs().median():.1%}   "
              f"within 5%: {(rr.abs() < 0.05).mean():.0%}   within 25%: {(rr.abs() < 0.25).mean():.0%}")

    print("\n  25 largest absolute disagreements, with the block group that drives each:")
    can_explain = block_shares is not None and area_shares is not None and block_groups is not None
    if can_explain:
        bs = block_shares.set_index(["bg_geoid", "place_geoid"])["share"]
        as_ = area_shares.set_index(["bg_geoid", "place_geoid"])["share"]
        pop = block_groups["b02001_001e"].fillna(0)
    top = both.reindex(both["diff"].abs().sort_values(ascending=False).index).head(25)
    for (pf, cf), row in top.iterrows():
        line = (f"    {row['Place Name_area']}, {row['State_area']} / {row['County Name_area']}: "
                f"block {row['Population_block']:,.0f}  area {row['Population_area']:,.0f}  "
                f"({row['diff']:+,.0f}, {row['rel']:+.0%})")
        if can_explain:
            bgs_here = [g for g in set(bs.loc[:, pf].index if pf in bs.index.get_level_values(1) else [])
                        if g[:5] == cf]
            bgs_here += [g for g in set(as_.loc[:, pf].index if pf in as_.index.get_level_values(1) else [])
                         if g[:5] == cf]
            # the block group pushing hardest in the direction of the row's total difference
            best, best_delta = None, 0.0
            for g in set(bgs_here):
                delta = (bs.get((g, pf), 0.0) - as_.get((g, pf), 0.0)) * pop.get(g, 0.0)
                if np.sign(delta) == np.sign(row["diff"]) and abs(delta) > abs(best_delta):
                    best, best_delta = g, delta
            if best is not None:
                line += (f"\n        driven by BG {best} (pop {pop.get(best, 0):,.0f}): block share "
                         f"{bs.get((best, pf), 0.0):.3f} vs area share {as_.get((best, pf), 0.0):.3f} "
                         f"→ {best_delta:+,.0f}")
        print(line)

    table = both.reset_index()[["Place FIPS", "Place Name_area", "Class_area", "State_area", "County FIPS",
                                "County Name_area", "Population_block", "Population_area", "diff", "rel",
                                "Scaled NWI_block", "Scaled NWI_area"]]
    table.columns = ["Place FIPS", "Place Name", "Class", "State", "County FIPS", "County Name",
                     "Population (block)", "Population (area)", "Difference", "Relative Difference",
                     "Scaled NWI (block)", "Scaled NWI (area)"]
    table.to_csv(BOUNDARIES / "compare_block_area.csv", index=False)
    return both

# ----------------------------------------------------------------------------
# Nesting check — geometric
# ----------------------------------------------------------------------------

def nesting_check(fips):
    """Overlay every 2010 block against the 2010 places and report the fraction of each block's
    area inside a place. Nesting predicts each fraction is ≈ 0 or ≈ 1. Also confirms the BAF's
    PLACEFP agrees with the place holding the block's area."""
    import geopandas as gpd

    crs = equal_area_crs(fips)
    blocks = gpd.read_file(POPHU_DIR / f"tabblock2010_{fips}_pophu.shp")[["BLOCKID10", "geometry"]].to_crs(crs)
    places = gpd.read_file(PLACES_DIR / f"tl_2010_{fips}_place10.shp")[["PLACEFP10", "geometry"]].to_crs(crs)
    blocks["block_area"] = blocks.geometry.area
    print(f"\n== Nesting check {STATES[fips]} ({crs}) — {len(blocks):,} blocks, {len(places):,} places ==")
    inter = gpd.overlay(blocks, places, how="intersection", keep_geom_type=True)
    inter["frac"] = inter.geometry.area / inter["block_area"]
    by_block_place = inter.groupby(["BLOCKID10", "PLACEFP10"])["frac"].sum().reset_index()
    inside = by_block_place.groupby("BLOCKID10")["frac"].sum()
    frac = inside.reindex(blocks["BLOCKID10"]).fillna(0.0)

    edges = [0, 1e-6, 1e-4, 1e-2, 0.1, 0.5, 0.9, 0.99, 1 - 1e-4, 1 - 1e-6, 1 + 1e-6, np.inf]
    hist = pd.cut(frac, edges, right=False).value_counts().sort_index()
    print("  fraction of block area inside any place:")
    for interval, n in hist.items():
        if n:
            print(f"      {str(interval):<24} {n:>9,}")
    between = frac[(frac > 1e-4) & (frac < 1 - 1e-4)]
    print(f"  blocks between 0.0001 and 0.9999: {len(between):,} of {len(frac):,}")

    baf = read_baf(fips).set_index("BLOCKID")["PLACEFP"]
    dominant = by_block_place.sort_values("frac").groupby("BLOCKID10").tail(1).set_index("BLOCKID10")
    geo_place = dominant["PLACEFP10"].where(dominant["frac"] >= 0.5)
    geo_place = geo_place.reindex(baf.index).fillna("")
    agree = (geo_place == baf)
    print(f"  BAF PLACEFP agrees with geometric containment: {agree.sum():,} of {len(baf):,} "
          f"({agree.mean():.5%}); disagreements: {(~agree).sum():,}")
    if (~agree).any():
        bad = pd.DataFrame({"baf": baf[~agree], "geometric": geo_place[~agree],
                            "frac_inside": frac.reindex(baf.index[~agree])})
        print(bad.head(15).to_string())
    return frac


# ----------------------------------------------------------------------------
# Place weights — the share tables as products (S21)
# ----------------------------------------------------------------------------

WEIGHT_COLUMNS = [
    "Block Group GEOID10", "Block Group GEOID20", "Place FIPS", "Place Name", "Class", "State",
    "County FIPS", "County Name", "Share", "Share Basis",
]
NWI_CSV = OUT_DIR / "nwi.csv"  # the EPA Smart Location Database export; carries both identifiers


def load_geoid20():
    """12-digit GEOID10 → GEOID20 from the SLD file. The 2019 identifier is the key to ACS 2015–2019;
    it differs from the 2010 identifier for the block groups the Bureau recoded after 2010."""
    ids = pd.read_csv(NWI_CSV, dtype=str, usecols=["GEOID10", "GEOID20"])
    ids["GEOID10"] = ids["GEOID10"].str.zfill(12)
    ids["GEOID20"] = ids["GEOID20"].str.zfill(12)
    assert ids["GEOID10"].is_unique
    return ids.set_index("GEOID10")["GEOID20"]


def read_share_table(method):
    """A share table as S18/S19 wrote it, or None if it has not been built."""
    path = BOUNDARIES / f"shares_{method}.csv"
    if not path.exists():
        return None
    shares = pd.read_csv(path, dtype={"bg_geoid": str, "place_geoid": str}, float_precision="round_trip")
    shares["bg_geoid"] = shares["bg_geoid"].str.zfill(12)
    shares["place_geoid"] = shares["place_geoid"].str.zfill(7)
    return shares


def format_weights(shares, places, county_names, geoid20):
    """The product table: one row per (block group, place), names joined, both identifiers, no ACS.

    Only block groups in the NWI are products. The 2010 TIGER files carry one block group the SLD
    does not (an all-water block group); its share rows are returned separately for the report.
    """
    in_nwi = shares["bg_geoid"].isin(geoid20.index)
    dropped = shares[~in_nwi]
    shares = shares[in_nwi]
    out = pd.DataFrame({
        "Block Group GEOID10": shares["bg_geoid"],
        "Block Group GEOID20": shares["bg_geoid"].map(geoid20),
        "Place FIPS": shares["place_geoid"],
        "Place Name": shares["place_geoid"].map(places["place_name"]),
        "Class": shares["place_geoid"].map(places["class"]),
        "State": shares["place_geoid"].str[:2].map(STATE_NAMES),
        "County FIPS": shares["bg_geoid"].str[:5],
        "County Name": shares["bg_geoid"].str[:5].map(county_names),
        "Share": shares["share"],
        "Share Basis": shares["share_basis"],
    })
    columns = list(WEIGHT_COLUMNS)
    if "pop2010" in shares.columns:
        out["Block Population 2010"] = shares["pop2010"].astype(int)
        columns.append("Block Population 2010")
    for c in ["Block Group GEOID20", "Place Name", "Class", "State", "County Name"]:
        assert out[c].notna().all(), f"{c}: {out[c].isna().sum()} rows could not be named"
    return out[columns].sort_values(["Block Group GEOID10", "Place FIPS"]).reset_index(drop=True), dropped


def read_weights(path):
    """A weights product read back into the share-table shape aggregate() takes."""
    w = pd.read_csv(path, dtype={"Block Group GEOID10": str, "Block Group GEOID20": str, "Place FIPS": str,
                                 "County FIPS": str}, float_precision="round_trip")
    shares = pd.DataFrame({"bg_geoid": w["Block Group GEOID10"], "place_geoid": w["Place FIPS"],
                           "share": w["Share"], "share_basis": w["Share Basis"]})
    if "Block Population 2010" in w.columns:
        shares["pop2010"] = w["Block Population 2010"]
    return w, shares


def verify_weights(method, product_path, block_groups, places, county_names, geoid20):
    """The invariants the card names, from the product file itself. Returns (ok, pairs)."""
    ok = True
    w, shares = read_weights(product_path)
    print(f"\n== {method}: {product_path.name} ==")
    print(f"  rows {len(w):,}   block groups {w['Block Group GEOID10'].nunique():,}   "
          f"places {w['Place FIPS'].nunique():,}   share basis {w['Share Basis'].value_counts().to_dict()}   "
          f"zero shares {(w['Share'] == 0).sum():,}")

    not_in_nwi = set(w["Block Group GEOID10"]) - set(block_groups.index)
    print(f"  block groups not in the NWI data: {len(not_in_nwi)}")
    ok &= not not_in_nwi
    ids = w.drop_duplicates("Block Group GEOID10")
    many = ids.groupby("Block Group GEOID10")["Block Group GEOID20"].nunique()
    n_recoded = (geoid20.index != geoid20.values).sum()
    n_here = (ids["Block Group GEOID10"] != ids["Block Group GEOID20"]).sum()
    print(f"  GEOID10 → GEOID20 one-to-one: {(many == 1).all()}   "
          f"GEOID20 differs for {n_here} block groups ({n_recoded} in the NWI; the other {n_recoded - n_here} touch no place)")
    ok &= bool((many == 1).all())

    sums = shares.groupby("bg_geoid")["share"].sum()
    over = (sums > 1 + SHARE_TOLERANCE).sum()
    print(f"  Σ share per block group: max {sums.max():.12f}   over 1: {over}   "
          f"whole: {(sums > 1 - SHARE_TOLERANCE).sum():,}   split: {(sums <= 1 - SHARE_TOLERANCE).sum():,}")
    ok &= over == 0

    # Round trip: aggregating the product reproduces the rolled-up export. The comparison is to
    # floating-point summation order: the same rows, the same rounded populations except where an
    # unrounded value sits on a .5, and the weighted NWI figures to 1e-9.
    export_path = OUT_DIR / f"export_places_by_county_{method}.csv"
    out, _ = aggregate(shares, block_groups, places, county_names)
    exp = pd.read_csv(export_path, dtype={"Place FIPS": str, "County FIPS": str})
    same_rows = out[["Place FIPS", "County FIPS"]].reset_index(drop=True).equals(exp[["Place FIPS", "County FIPS"]])
    print(f"  round trip → {export_path.name}: same {len(exp):,} rows in the same order: {same_rows}")
    ok &= same_rows
    if same_rows:
        for c in ["Population"] + NWI_LEVEL_COLUMNS:
            raw = out[c].to_numpy()
            diff = np.flatnonzero(np.round(raw).astype(int) != exp[c].to_numpy())
            half = [i for i in diff if abs(abs(raw[i] - np.floor(raw[i])) - 0.5) < 1e-6]
            print(f"      {c:<18} rounded values differ in {len(diff)} rows; {len(half)} of them sit on a .5")
            for i in half[:6]:
                print(f"          {exp.loc[i, 'Place Name']}, {exp.loc[i, 'County Name']}: "
                      f"{raw[i]:.9f} here, {exp.loc[i, c]} in the export")
            ok &= len(diff) == len(half)
        for c in ["Raw NWI", "Scaled NWI", "Share of Place Population"]:
            d = (out[c].to_numpy() - exp[c].to_numpy())
            d = np.nanmax(np.abs(d)) if np.isfinite(d).any() else 0.0
            print(f"      {c:<26} max |Δ| {d:.2e}")
            ok &= d < 1e-9
    return ok, set(zip(shares["bg_geoid"], shares["place_geoid"]))


def build_weights():
    missing = [m for m in ("block", "area") if not (BOUNDARIES / f"shares_{m}.csv").exists()]
    if missing:
        print(f"share table(s) not built: {missing} — run `build {missing[0]}` first (S18/S19)")
        return False
    print("Loading block groups, places, counties, identifiers...")
    block_groups = load_block_groups()
    places = load_places()
    county_names, _, _, _ = load_county_names(block_groups)
    geoid20 = load_geoid20()
    print(f"  {len(block_groups):,} block groups; {len(places):,} places; "
          f"{(geoid20.index != geoid20.values).sum()} block groups whose GEOID20 differs from GEOID10")

    ok, pairs = True, {}
    for method in ("block", "area"):
        shares = read_share_table(method)
        product, dropped = format_weights(shares, places, county_names, geoid20)
        path = OUT_DIR / f"export_place_weights_{method}.csv"
        product.to_csv(path, index=False)
        print(f"\nwrote {len(product):,} rows to {path}")
        for _, r in dropped.iterrows():
            print(f"  not in the NWI, left out: block group {r['bg_geoid']} ({county_names.get(r['bg_geoid'][:5], '?')}), "
                  f"place {r['place_geoid']} {places['place_name'].get(r['place_geoid'], '?')}, share {r['share']:.6f}, "
                  f"basis {r['share_basis']}")
        good, pairs[method] = verify_weights(method, path, block_groups, places, county_names, geoid20)
        ok &= good

    print("\n== Pair sets ==")
    only_block = pairs["block"] - pairs["area"]
    only_area = pairs["area"] - pairs["block"]
    print(f"  pairs in both files: {len(pairs['block'] & pairs['area']):,}   block only: {len(only_block)}   "
          f"area only: {len(only_area)}")
    for bg, pl in sorted(only_block):
        print(f"      block only: block group {bg}, place {pl} {places['place_name'].get(pl, '?')} "
              f"({county_names.get(bg[:5], '?')}) — the piece under the area noise floor")
    for bg, pl in sorted(only_area):
        print(f"      area only:  block group {bg}, place {pl} {places['place_name'].get(pl, '?')}")
    print("\nALL INVARIANTS HOLD" if ok else "\nINVARIANT FAILURES — see above")
    return ok


# ----------------------------------------------------------------------------

SHARE_BUILDERS = {"block": block_share_table, "area": area_share_table}


def build(method):
    print("Loading block groups, places, counties...")
    block_groups = load_block_groups()
    places = load_places()
    county_names, disagreements, n_db, n_shp = load_county_names(block_groups)
    print(f"  {len(block_groups):,} block groups; {len(places):,} places; county names: {n_db} from the "
          f"database, {n_shp} from the shapefile, {len(disagreements)} disagree")
    for f, (a, b) in list(disagreements.items())[:20]:
        print(f"      {f}: db '{a}'  shp '{b}'")

    print(f"Share tables by state ({method})...")
    share_frames, basis_frames, overlay_stats = [], [], []
    for fips in sorted(STATES):
        if method == "block":
            s, basis = block_share_table(fips)
            basis_frames.append(basis)
            print(f"  {STATES[fips]} {len(s):>7,} (bg, place) rows")
        else:
            s, st = area_share_table(fips)
            overlay_stats.append(st)
            print(f"  {STATES[fips]} {len(s):>7,} (bg, place) rows   pieces {st['pieces']:>7,}  "
                  f"dropped <{NOISE_FLOOR_M2:g} m²: {st['pieces_dropped']:>5,} ({st['area_dropped_m2']:.1f} m²)  "
                  f"invalid bg/places: {st['invalid_bg']}/{st['invalid_places']}  "
                  f"place overlap: {st['place_overlap_m2']:.1f} m²")
        share_frames.append(s)
    shares = pd.concat(share_frames, ignore_index=True)
    basis_by_bg = pd.concat(basis_frames) if basis_frames else None
    shares.to_csv(BOUNDARIES / f"shares_{method}.csv", index=False)

    print("Aggregating...")
    out, pieces = aggregate(shares, block_groups, places, county_names)
    path = OUT_DIR / f"export_places_by_county_{method}.csv"
    write_output(out, path)
    print(f"  wrote {len(out):,} rows to {path}")

    ok = verify(shares, out, pieces, block_groups, basis_by_bg)
    if overlay_stats:
        st = pd.DataFrame(overlay_stats)
        print("\n== Overlay totals ==")
        print(f"  pieces {st['pieces'].sum():,}; dropped under {NOISE_FLOOR_M2:g} m²: {st['pieces_dropped'].sum():,} "
              f"totalling {st['area_dropped_m2'].sum():.2f} m²; invalid geometries repaired: "
              f"{st['invalid_bg'].sum():,} block groups, {st['invalid_places'].sum():,} places; "
              f"place-on-place overlap: {st['place_overlap_m2'].sum():.2f} m² "
              f"(max in one state {st['place_overlap_m2'].max():.2f})")
        print(f"  pieces under 0.1% of their block group: {st['pieces_under_0.1pct'].sum():,}; "
              f"under 1%: {st['pieces_under_1pct'].sum():,}")
    spot_check_multi_county(out, pieces)
    if method == "block":
        compare_single_county(out, places)
    else:
        block_shares_path = BOUNDARIES / "shares_block.csv"
        block_shares = (pd.read_csv(block_shares_path, dtype={"bg_geoid": str, "place_geoid": str})
                        if block_shares_path.exists() else None)
        compare_products(block_shares, shares, block_groups)
    print("\nALL INVARIANTS HOLD" if ok else "\nINVARIANT FAILURES — see above")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        method = sys.argv[2] if len(sys.argv) > 2 else "block"
        assert method in SHARE_BUILDERS, f"method must be one of {list(SHARE_BUILDERS)}"
        sys.exit(0 if build(method) else 1)
    elif cmd == "nesting":
        for fips in sys.argv[2:] or ["10"]:
            nesting_check(fips)
    elif cmd == "slivers":
        for fips in sys.argv[2:] or ["10"]:
            sliver_report(fips)
    elif cmd == "weights":
        sys.exit(0 if build_weights() else 1)
    elif cmd == "compare":
        block_groups = load_block_groups()
        dt = {"bg_geoid": str, "place_geoid": str}
        compare_products(pd.read_csv(BOUNDARIES / "shares_block.csv", dtype=dt),
                         pd.read_csv(BOUNDARIES / "shares_area.csv", dtype=dt), block_groups)
    else:
        sys.exit(__doc__)
