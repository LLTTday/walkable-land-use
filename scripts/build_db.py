#!/usr/bin/env python3
"""
Build the block group database from source.

`nwi_analysis/data/nwi_full_2019_complete.db` joins the EPA Smart Location
Database (the National Walkability Index, on 2010 block groups) to the ACS
2015–2019 five-year estimates. This script is the reproducible build that
replaces the hand-run notebook and the deleted July 2025 script that made the
current database.

The vintage rule (methodology, "Geography vintages"): boundary files join on
GEOID10; the ACS joins on GEOID20. The SLD carries both identifiers. The ACS is
published on 2019-vintage identifiers and the Bureau recoded 73 block groups
between 2010 and 2019; joining the ACS on GEOID10 left those 73 without data.

    uv run python v2/scripts/build_db.py pull [FIPS ...]   # ACS per state → boundaries/acs/{FIPS}.csv (needs CENSUS_API_KEY)
    uv run python v2/scripts/build_db.py build             # assemble → data/nwi_full_2019_complete.new.db, diff against the current database
    uv run python v2/scripts/build_db.py install           # replace the current database with the new one

Inputs:
    nwi_analysis/data/nwi.csv                       the SLD export: both identifiers, NatWalkInd, CSA/CBSA names
    boundaries/acs/{FIPS}.csv                       the ACS pull, one file per state, written by `pull`
    boundaries/acs/counties_2010.csv                2010 Census (SF1) county names, written by `pull`
    nwi_analysis/data/nwi_full_2019_complete.db     the current database: the diff baseline, and the source of the
                                                    one column carried forward (see CARRIED_FORWARD)

County names come from the 2010 SF1, the source the original notebook used, keyed
on the GEOID10 county: they reproduce every name in the September 2025 database
exactly, and fill the rows it left blank (the 73 recoded block groups, the
District of Columbia, Puerto Rico's municipios). COUNTY_NAME_OVERRIDES holds the
three deliberate departures from the SF1 text.

What is carried forward rather than rebuilt: `city_name` (a PostGIS overlap of the
CDC 500-cities boundaries, which no longer exists), keyed on GEOID10 and copied
from the current database unchanged. Everything else — every ACS column, the age
and education aggregates, the walkability level and scale, the county names — is
computed here from source.

The API key is read from CENSUS_API_KEY. It is never written to disk by this script.
"""

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import json
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "nwi_analysis" / "data"
NWI_CSV = DATA / "nwi.csv"
CURRENT_DB = DATA / "nwi_full_2019_complete.db"
NEW_DB = DATA / "nwi_full_2019_complete.new.db"
ACS_DIR = Path(__file__).resolve().parent / "boundaries" / "acs"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from place_county import STATES, STATE_NAMES  # noqa: E402

ACS_URL = "https://api.census.gov/data/2019/acs/acs5"
TERRITORIES = {"60", "66", "69", "78"}  # no ACS; stay in the NWI with null demographics

# ----------------------------------------------------------------------------
# The schema. Column order and declared types match the current database so
# that no consumer changes. INT columns are ACS counts; REAL columns are sums
# over several ACS counts (null where the block group has no ACS row).
# ----------------------------------------------------------------------------

ACS_COUNTS = [  # stored as pulled
    "b02001_001e", "b02001_002e", "b02001_003e", "b02001_004e", "b02001_005e", "b02001_006e", "b02001_007e", "b02001_008e",
    "b03001_002e", "b03001_003e", "b03001_009e", "b03001_010e", "b03002_002e", "b03002_003e",
    "b06009_002e", "b06009_003e", "b06009_004e", "b06009_005e", "b06009_006e", "b06009_007e",
    "b08301_002e", "b08301_003e", "b08301_010e", "b08301_018e", "b08301_019e", "b08301_020e", "b08301_021e",
    "b11001_002e", "b11001_003e", "b11001_004e", "b11001_005e", "b11001_006e", "b11001_007e", "b11001_008e", "b11001_009e",
    "b15003_021e", "b15003_022e", "b15003_023e", "b15003_024e", "b15003_025e",
    "b19001_002e", "b19001_003e", "b19001_004e", "b19001_005e", "b19001_006e", "b19001_007e", "b19001_008e", "b19001_009e",
    "b19001_010e", "b19001_011e", "b19001_012e", "b19001_013e", "b19001_014e", "b19001_015e", "b19001_016e", "b19001_017e",
    "b25003_002e", "b25003_003e", "b25008_002e", "b25008_003e",
]

# B01001 (sex by age): male cells 003–025, female cells 027–049. The aggregates pair each male
# cell with its female counterpart (offset 24). These are the notebook's groupings, in full.
AGE_GROUPS = {
    "b01001_003z": [3, 4, 5, 6],        # under 18
    "b01001_004z": [7, 8, 9, 10],       # 18–24
    "b01001_005z": [11, 12],            # 25–34
    "b01001_006z": [13, 14],            # 35–44
    "b01001_007z": [15, 16],            # 45–54
    "b01001_008z": [17, 18, 19],        # 55–64
    "b01001_009z": [20, 21, 22],        # 65–74
    "b01001_010z": [23, 24],            # 75–84
    "b01001_011z": [25],                # 85 and over
}
AGE_RAW = sorted({f"B01001_{c:03d}E" for cells in AGE_GROUPS.values() for m in cells for c in (m, m + 24)})

# B15003 (educational attainment, 25 and over): the three aggregates below the associate's degree.
EDU_GROUPS = {
    "b15003_002z": list(range(2, 17)),  # less than high school: no schooling … 12th grade no diploma
    "b15003_003z": [17, 18],            # high school diploma, GED
    "b15003_004z": [19, 20],            # some college, no degree
}
EDU_RAW = sorted({f"B15003_{c:03d}E" for cells in EDU_GROUPS.values() for c in cells})

# Universe totals, pulled for the row checks only (age cells must sum to B01001_001E; education
# cells to B15003_001E). Not stored.
CHECK_ONLY = ["B01001_001E", "B15003_001E"]

PULL_VARIABLES = sorted({c.upper() for c in ACS_COUNTS} | set(AGE_RAW) | set(EDU_RAW) | set(CHECK_ONLY))
# B06009 is a tract-level table; the API returns nothing for it at block group. The current
# database has it null in every row. It is requested in its own chunk so its failure costs nothing.
TRACT_ONLY = [v for v in PULL_VARIABLES if v.startswith("B06009")]

COLUMNS = [  # (name, declared type) in the current database's order
    ("geoid10", "TEXT"), ("objectid", "INT"), ("state_name", "TEXT"), ("county_name", "TEXT"),
    ("csa_name", "TEXT"), ("cbsa_name", "TEXT"), ("city_name", "TEXT"), ("geography_type", "TEXT"),
    ("nwi", "INT"), ("natwalkind", "REAL"), ("nwi_scaled_10", "REAL"),
] + [(c, "REAL") for c in AGE_GROUPS] + [(c, "INT") for c in ACS_COUNTS if c < "b15003_002z"] \
  + [(c, "REAL") for c in EDU_GROUPS] + [(c, "INT") for c in ACS_COUNTS if c > "b15003_002z"]
assert len(COLUMNS) == len({c for c, _ in COLUMNS}) == 11 + 9 + 3 + len(ACS_COUNTS), len(COLUMNS)

CARRIED_FORWARD = ["city_name"]

SF1_URL = "https://api.census.gov/data/2010/dec/sf1"
COUNTIES_2010 = ACS_DIR / "counties_2010.csv"

# The SF1 text is used as published except here. The two counties renamed and recoded in 2015
# keep their 2010 name, as the 2010 geography requires, with the current name beside it so they
# can be found under the name in use. The SF1 repeats the state for the District of Columbia;
# every consumer already calls it "District of Columbia".
COUNTY_NAME_OVERRIDES = {
    "46113": "Shannon County (now Oglala Lakota County), South Dakota",
    "02270": "Wade Hampton Census Area (now Kusilvak Census Area), Alaska",
    "11001": "District of Columbia",
}


# ----------------------------------------------------------------------------
# pull — the ACS, one file per state
# ----------------------------------------------------------------------------

def api_key():
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        sys.exit("CENSUS_API_KEY is not set. Get one at https://api.census.gov/data/key_signup.html and export it.")
    return key


def fetch(variables, fips, key, retries=4):
    """One API request: the variables for every block group of one state. Returns a DataFrame
    keyed by the ACS geoid (state + county + tract + block group), or None if the API has no data."""
    params = {
        "get": ",".join(["GEO_ID"] + variables),
        "for": "block group:*",
        "in": f"state:{fips} county:* tract:*",
        "key": key,
    }
    url = ACS_URL + "?" + urllib.parse.urlencode(params)
    status, body = None, ""
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                status, body = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, body = e.code, e.read().decode(errors="replace")
        except urllib.error.URLError:
            status, body = None, "no response"
        if status == 204:
            return None
        if status == 200:
            rows = json.loads(body)
            df = pd.DataFrame(rows[1:], columns=rows[0])
            df["acs_geoid"] = df["state"] + df["county"] + df["tract"] + df["block group"]
            return df.drop(columns=["state", "county", "tract", "block group"])
        if status == 400 and "unknown variable" in body.lower():
            raise RuntimeError(f"{fips}: the API rejected a variable: {body[:200]}")
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{fips}: API failed after {retries} attempts: HTTP {status} {body[:200]}")


def chunks(variables, size=48):  # the API allows 50 variables per request; GEO_ID takes one slot
    return [variables[i:i + size] for i in range(0, len(variables), size)]


def pull(fips_list):
    key = api_key()
    ACS_DIR.mkdir(parents=True, exist_ok=True)
    pull_county_names(key)
    regular = [v for v in PULL_VARIABLES if v not in TRACT_ONLY]
    for fips in fips_list:
        out = ACS_DIR / f"{fips}.csv"
        if out.exists():
            print(f"  {STATES[fips]} already pulled ({out.name})")
            continue
        frames = []
        for part in chunks(regular):
            df = fetch(part, fips, key)
            if df is None:
                raise RuntimeError(f"{fips}: no block group data returned")
            frames.append(df.set_index("acs_geoid"))
        acs = pd.concat(frames, axis=1)
        acs = acs.loc[:, ~acs.columns.duplicated()]  # GEO_ID repeats per chunk
        tract_only = fetch(TRACT_ONLY, fips, key) if TRACT_ONLY else None
        if tract_only is not None and len(tract_only):
            acs = acs.join(tract_only.set_index("acs_geoid")[TRACT_ONLY], how="left")
        else:
            for v in TRACT_ONLY:
                acs[v] = pd.NA
        assert acs.index.is_unique, f"{fips}: duplicate block groups in the ACS response"
        acs.to_csv(out, index_label="acs_geoid")
        print(f"  {STATES[fips]} {len(acs):>7,} block groups → {out.name}")


def pull_county_names(key):
    if COUNTIES_2010.exists():
        print(f"  county names already pulled ({COUNTIES_2010.name})")
        return
    params = {"get": "NAME", "for": "county:*", "key": key}
    with urllib.request.urlopen(SF1_URL + "?" + urllib.parse.urlencode(params), timeout=120) as r:
        rows = json.loads(r.read().decode())
    df = pd.DataFrame(rows[1:], columns=rows[0])
    df["county_fips"] = df["state"] + df["county"]
    df[["county_fips", "NAME"]].to_csv(COUNTIES_2010, index=False)
    print(f"  2010 county names: {len(df):,} → {COUNTIES_2010.name}")


def county_names():
    if not COUNTIES_2010.exists():
        sys.exit(f"missing {COUNTIES_2010.name}; run `pull` first")
    names = pd.read_csv(COUNTIES_2010, dtype=str).set_index("county_fips")["NAME"].to_dict()
    names.update(COUNTY_NAME_OVERRIDES)
    return names


def load_acs():
    frames = []
    for fips in sorted(STATES):
        path = ACS_DIR / f"{fips}.csv"
        if not path.exists():
            sys.exit(f"missing {path.name}; run `pull` first")
        frames.append(pd.read_csv(path, dtype={"acs_geoid": str, "GEO_ID": str}))
    acs = pd.concat(frames, ignore_index=True)
    assert acs["acs_geoid"].str.len().eq(12).all()
    assert acs["acs_geoid"].is_unique
    for v in PULL_VARIABLES:
        acs[v] = pd.to_numeric(acs[v], errors="coerce")
    return acs.set_index("acs_geoid")


# ----------------------------------------------------------------------------
# build — assemble the table
# ----------------------------------------------------------------------------

def load_sld():
    sld = pd.read_csv(NWI_CSV, dtype={"GEOID10": str, "GEOID20": str, "CSA_Name": str, "CBSA_Name": str},
                      usecols=["OBJECTID", "GEOID10", "GEOID20", "CSA_Name", "CBSA_Name", "NatWalkInd"])
    sld["GEOID10"] = sld["GEOID10"].str.zfill(12)
    sld["GEOID20"] = sld["GEOID20"].str.zfill(12)
    assert sld["GEOID10"].is_unique and sld["GEOID20"].is_unique
    return sld


def walkability_level(natwalkind):
    """The EPA's four levels on the 1–20 index, the top bound inclusive (as the current database has it)."""
    level = pd.Series(pd.NA, index=natwalkind.index, dtype="Int64")
    level[(natwalkind >= 1.0) & (natwalkind < 5.75)] = 0
    level[(natwalkind >= 5.75) & (natwalkind < 10.5)] = 1
    level[(natwalkind >= 10.5) & (natwalkind < 15.25)] = 2
    level[(natwalkind >= 15.25) & (natwalkind <= 20.0)] = 3
    return level


def aggregate_sums(acs, groups, table):
    """Sum ACS cells into a REAL column, null where the row has no ACS data."""
    out = {}
    for col, cells in groups.items():
        if table == "B01001":
            raw = [f"B01001_{c:03d}E" for m in cells for c in (m, m + 24)]
        else:
            raw = [f"{table}_{c:03d}E" for c in cells]
        out[col] = acs[raw].sum(axis=1, min_count=len(raw)).astype(float)
    return pd.DataFrame(out, index=acs.index)


def build():
    print("Loading the SLD, the ACS pull, and the current database...")
    sld = load_sld()
    acs = load_acs()
    with sqlite3.connect(CURRENT_DB) as conn:
        carried = pd.read_sql(f"SELECT geoid10, {', '.join(CARRIED_FORWARD)} FROM nwi_full", conn).set_index("geoid10")
    print(f"  SLD {len(sld):,} block groups; ACS {len(acs):,} block groups; "
          f"GEOID20 differs from GEOID10 for {(sld['GEOID10'] != sld['GEOID20']).sum()}")

    # The join: ACS rows found through GEOID20, the table keyed on GEOID10.
    joined = sld.join(acs, on="GEOID20", how="left")
    matched = joined["GEO_ID"].notna()
    state = joined["GEOID10"].str[:2]
    unmatched_states = joined.loc[~matched, "GEOID10"].str[:2].value_counts().to_dict()
    print(f"  ACS rows matched: {matched.sum():,}; unmatched: {(~matched).sum():,} by state {unmatched_states}")
    assert set(unmatched_states) <= TERRITORIES, f"unmatched block groups outside the territories: {unmatched_states}"
    acs_not_in_sld = acs.index.difference(sld["GEOID20"])
    print(f"  ACS block groups not in the SLD: {len(acs_not_in_sld)} {list(acs_not_in_sld)[:5]}")

    print("Deriving columns...")
    t = pd.DataFrame(index=joined.index)
    t["geoid10"] = joined["GEOID10"]
    t["objectid"] = joined["OBJECTID"].astype(int)
    t["state_name"] = state.map(STATE_NAMES)
    t["county_name"] = joined["GEOID10"].str[:5].map(county_names())
    for c in CARRIED_FORWARD:
        t[c] = joined["GEOID10"].map(carried[c])
    t["csa_name"] = joined["CSA_Name"]
    t["cbsa_name"] = joined["CBSA_Name"]
    t["geography_type"] = "block_group"
    t["nwi"] = walkability_level(joined["NatWalkInd"])
    t["natwalkind"] = joined["NatWalkInd"].astype(float)
    t["nwi_scaled_10"] = (t["natwalkind"] - 1.0) / (20.0 - 1.0) * 9.0 + 1.0
    ages = aggregate_sums(joined, AGE_GROUPS, "B01001")
    edus = aggregate_sums(joined, EDU_GROUPS, "B15003")
    for c in AGE_GROUPS:
        t[c] = ages[c]
    for c in EDU_GROUPS:
        t[c] = edus[c]
    for c in ACS_COUNTS:
        t[c] = joined[c.upper()].astype("Int64")

    # Row checks against the universe totals: the nine age cells partition B01001_001E and the
    # eight education cells partition B15003_001E, for every block group with an ACS row.
    age_sum = t[list(AGE_GROUPS)].sum(axis=1, min_count=9)
    edu_sum = t[list(EDU_GROUPS)].sum(axis=1, min_count=3) + joined[[f"B15003_{c:03d}E" for c in range(21, 26)]].sum(axis=1)
    age_ok = (age_sum == joined["B01001_001E"]) | ~matched
    edu_ok = (edu_sum == joined["B15003_001E"]) | ~matched
    print(f"  age cells sum to B01001_001E: {age_ok.sum():,} of {len(t):,}  (mismatches {(~age_ok).sum()})")
    print(f"  education cells sum to B15003_001E: {edu_ok.sum():,} of {len(t):,}  (mismatches {(~edu_ok).sum()})")
    assert age_ok.all() and edu_ok.all()

    t = t[[c for c, _ in COLUMNS]]
    print(f"Writing {NEW_DB.name}...")
    if NEW_DB.exists():
        NEW_DB.unlink()
    with sqlite3.connect(NEW_DB) as conn:
        conn.execute("CREATE TABLE nwi_full (" + ", ".join(f'"{c}" {ty}' for c, ty in COLUMNS) + ")")
        rows = t.astype(object).where(t.notna(), None).itertuples(index=False, name=None)
        conn.executemany(f"INSERT INTO nwi_full VALUES ({', '.join('?' * len(COLUMNS))})", rows)
        conn.execute("CREATE INDEX idx_nwi_full_geoid10 ON nwi_full(geoid10)")
        conn.commit()
    return diff()


# ----------------------------------------------------------------------------
# diff — the gate
# ----------------------------------------------------------------------------

def read_table(path):
    with sqlite3.connect(path) as conn:
        df = pd.read_sql("SELECT * FROM nwi_full", conn)
    return df.set_index("geoid10").sort_index()


def diff():
    """Every column of the new table against the current one, keyed on GEOID10."""
    print("\n== Diff: new database against the current one ==")
    old, new = read_table(CURRENT_DB), read_table(NEW_DB)
    assert list(old.columns) == list(new.columns), (set(old.columns) ^ set(new.columns))
    assert old.index.equals(new.index), "the two tables do not have the same block groups"
    recoded = load_sld().query("GEOID10 != GEOID20")["GEOID10"]
    print(f"  {len(old):,} rows, {len(old.columns)} columns; {len(recoded)} recoded block groups")

    ok = True
    print(f"  {'column':<16}{'differ':>9}{'null→value':>12}{'value→null':>12}{'value→value':>13}  note")
    for c in old.columns:
        a, b = old[c], new[c]
        if pd.api.types.is_numeric_dtype(a) or pd.api.types.is_numeric_dtype(b):
            a, b = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
            same = (a == b) | (a.isna() & b.isna())
        else:
            same = (a == b) | (a.isna() & b.isna())
        d = ~same
        n2v = (a.isna() & b.notna() & d).sum()
        v2n = (a.notna() & b.isna() & d).sum()
        v2v = (a.notna() & b.notna() & d).sum()
        if d.sum() == 0:
            note = ""
        elif c in ("b01001_006z", "b01001_007z", "b01001_008z", "b01001_009z", "b01001_010z"):
            # The old build's pull omitted these cells and filled them with 0 in every row, the
            # territories included. Expected now: the old column is 0 throughout; the new one is
            # null in the territories (as every other ACS column is) and summed everywhere else.
            note = "age 35–84: zero in every row of the old build (its pull omitted these cells); now summed"
            territory = new.index.str[:2].isin(TERRITORIES)
            if not ((a == 0).all() and b[territory].isna().all() and b[~territory].notna().all()):
                ok = False
                note += "  UNEXPECTED pattern"
        elif c == "county_name":
            # Every name the old build had is reproduced; the blanks it left are filled, except
            # the territories, which the SF1 does not cover.
            territory = new.index.str[:2].isin(TERRITORIES)
            note = "names reproduced exactly; blanks filled from the 2010 SF1 (recoded, DC, PR)"
            if not (v2v == 0 and v2n == 0 and set(d[d].index) == set(old.index[a.isna() & ~territory])):
                ok = False
                note += "  UNEXPECTED pattern"
        elif c.startswith("b") and c[1:6].isdigit():
            note = "the 73 recoded block groups" if set(d[d].index) == set(recoded) else "UNEXPECTED rows"
            ok &= set(d[d].index) == set(recoded)
        else:
            note = "UNEXPECTED"
            ok = False
        if d.sum():
            print(f"  {c:<16}{d.sum():>9,}{n2v:>12,}{v2n:>12,}{v2v:>13,}  {note}")
    unchanged = [c for c in old.columns if ((old[c] == new[c]) | (old[c].isna() & new[c].isna())).all()]
    print(f"  identical columns ({len(unchanged)}): {', '.join(unchanged)}")

    print("\n== The recoded block groups ==")
    r_old, r_new = old.loc[recoded], new.loc[recoded]
    print(f"  ACS population before: {r_old['b02001_001e'].notna().sum()} rows populated; after: {r_new['b02001_001e'].notna().sum()}")
    for label, fips in [("Oglala Lakota County (46113)", "46113"), ("Kusilvak Census Area (02270)", "02270"),
                        ("Bedford city (51515)", "51515"), ("Madison County NY (36053)", "36053"),
                        ("Oneida County NY (36065)", "36065"), ("Pima County AZ (04019)", "04019"),
                        ("Los Angeles County (06037)", "06037"), ("Richmond County NY (36085)", "36085")]:
        sub = r_new[r_new.index.str[:5] == fips]
        print(f"  {label:<32} {len(sub):>3} block groups   ACS population {sub['b02001_001e'].sum():>9,.0f}")

    print("\n== Totals ==")
    for label, mask in [("national", slice(None))] + [(STATE_NAMES[s], old.index.str[:2] == s) for s in ["46", "02", "51", "36", "04", "06"]]:
        o = pd.to_numeric(old.loc[mask, "b02001_001e"]).sum()
        n = pd.to_numeric(new.loc[mask, "b02001_001e"]).sum()
        print(f"  {label:<22} population {o:>13,.0f} → {n:>13,.0f}   +{n - o:,.0f}")
    terr = new.index.str[:2].isin(TERRITORIES)
    print(f"  territory rows: {terr.sum()}  with ACS: {new.loc[terr, 'b02001_001e'].notna().sum()}")
    ok &= terr.sum() == 406 and new.loc[terr, "b02001_001e"].notna().sum() == 0
    ages = new[list(AGE_GROUPS)].sum()
    print("  age totals, new: " + "  ".join(f"{c[-4:-1]}={v:,.0f}" for c, v in ages.items()))
    print(f"  Σ age cells {ages.sum():,.0f} vs Σ population {pd.to_numeric(new['b02001_001e']).sum():,.0f}")
    print("\nDIFF AS EXPECTED" if ok else "\nDIFF HAS UNEXPECTED CHANGES — see above")
    return ok


# ----------------------------------------------------------------------------
# install — replace the current database
# ----------------------------------------------------------------------------

def install():
    """Replace the current database with the new one. Only `nwi_full` is read by any consumer; the
    old `nwi_data` table (the July 2025 build's intermediate, read by nothing) is not carried."""
    if not NEW_DB.exists():
        sys.exit(f"{NEW_DB.name} does not exist; run `build` first")
    with sqlite3.connect(NEW_DB) as conn:
        conn.execute("VACUUM")
    backup = CURRENT_DB.with_suffix(".db.previous")
    shutil.move(CURRENT_DB, backup)
    shutil.move(NEW_DB, CURRENT_DB)
    print(f"installed {CURRENT_DB.name} ({CURRENT_DB.stat().st_size / 1e6:.0f} MB); "
          f"previous database at {backup.name} (not tracked)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "pull":
        fips_list = sys.argv[2:] or sorted(STATES)
        pull(fips_list)
    elif cmd == "build":
        sys.exit(0 if build() else 1)
    elif cmd == "diff":
        sys.exit(0 if diff() else 1)
    elif cmd == "install":
        install()
    else:
        sys.exit(__doc__)
