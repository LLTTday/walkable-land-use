#!/usr/bin/env python3
"""
Data accuracy verification for v2 walkability tool.

Reads SQLite source database directly and compares against the generated JSON
files. Reports any mismatches with jurisdiction, field, expected vs actual.

Usage: python v2/scripts/verify.py
"""

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent.parent.parent / "nwi_analysis" / "data" / "nwi_full_2019_complete.db"
DATA = Path(__file__).parent.parent / "public" / "data"

# Must mirror aggregate.py exactly
RACE_COLS = {
    "white": "b02001_002e",
    "black": "b02001_003e",
    "native_american": "b02001_004e",
    "asian": "b02001_005e",
    "pacific_islander": "b02001_006e",
    "other": "b02001_007e",
    "two_or_more": "b02001_008e",
}

INCOME_BUCKETS = {
    "under_25k": ["b19001_002e", "b19001_003e", "b19001_004e", "b19001_005e"],
    "25k_50k": ["b19001_006e", "b19001_007e", "b19001_008e", "b19001_009e", "b19001_010e"],
    "50k_100k": ["b19001_011e", "b19001_012e", "b19001_013e"],
    "over_100k": ["b19001_014e", "b19001_015e", "b19001_016e", "b19001_017e"],
}

HOMEOWNERSHIP_COLS = {
    "owner": "b25008_002e",
    "renter": "b25008_003e",
}

TRANSPORT_COLS = {
    "drove_alone": "b08301_003e",
    "transit": "b08301_010e",
    "walking": "b08301_019e",
    "bicycle": "b08301_018e",
    "wfh": "b08301_021e",
    "other": "b08301_020e",
}

AGE_COLS = {
    "under_18": "b01001_003z",
    "18_24": "b01001_004z",
    "25_34": "b01001_005z",
    "35_44": "b01001_006z",
    "45_54": "b01001_007z",
    "55_64": "b01001_008z",
    "65_74": "b01001_009z",
    "75_84": "b01001_010z",
    "85_plus": "b01001_011z",
}

EDUCATION_COLS = {
    "less_than_hs": "b15003_002z",
    "hs_grad": "b15003_003z",
    "some_college": "b15003_004z",
    "associates": "b15003_021e",
    "bachelors": "b15003_022e",
    "masters": "b15003_023e",
    "professional": "b15003_024e",
    "doctorate": "b15003_025e",
}

# Counties patched manually in aggregate.py — skip SQL verification for these
PATCHED_COUNTIES = {"46113", "02270", "51515"}

# Tolerance for floating point avg_nwi comparison
NWI_TOLERANCE = 0.015


def to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


class Verifier:
    def __init__(self):
        self.conn = sqlite3.connect(str(DB))
        self.conn.row_factory = sqlite3.Row
        self.errors = []
        self.warnings = []
        self.checks = 0

    def error(self, msg):
        self.errors.append(msg)
        print(f"  FAIL: {msg}")

    def warn(self, msg):
        self.warnings.append(msg)
        print(f"  WARN: {msg}")

    def ok(self):
        self.checks += 1

    def check_equal(self, label, expected, actual, tolerance=0):
        self.checks += 1
        if tolerance:
            if abs(expected - actual) > tolerance:
                self.error(f"{label}: expected {expected}, got {actual} (diff {actual - expected:+.4f})")
                return False
        else:
            if expected != actual:
                self.error(f"{label}: expected {expected}, got {actual} (diff {actual - expected:+})")
                return False
        return True

    def query_block_groups(self, where_clause, params=()):
        """Get block group rows matching a condition."""
        return self.conn.execute(
            f"SELECT * FROM nwi_full WHERE geography_type = 'block_group' AND {where_clause}",
            params,
        ).fetchall()

    def sum_col(self, rows, col):
        """Sum a column across rows, handling TEXT values."""
        return sum(to_float(r[col]) for r in rows)

    def sum_cols(self, rows, cols):
        """Sum multiple columns across rows."""
        return sum(to_float(r[c]) for r in rows for c in cols)

    def compute_carpool(self, rows):
        """Compute carpool = total vehicles - drove alone."""
        return sum(to_float(r["b08301_002e"]) - to_float(r["b08301_003e"]) for r in rows)

    def compute_hispanic(self, rows):
        """Compute hispanic = total - non_hispanic (where non_hisp > 0)."""
        total = 0
        for r in rows:
            pop = to_float(r["b02001_001e"])
            non_hisp = to_float(r["b03002_002e"])
            if non_hisp > 0:
                total += pop - non_hisp
        return total

    def verify_jurisdiction(self, label, json_data, rows, skip_demographics=False):
        """Verify a single jurisdiction's data against its block group rows."""
        # Population total
        expected_pop = round(self.sum_col(rows, "b02001_001e"))
        self.check_equal(f"{label} population", expected_pop, json_data["population"])

        # Internal consistency: sum of NWI level pops == total pop
        nwi_pop_sum = sum(json_data["by_nwi"].get(str(i), {}).get("population", 0) for i in range(4))
        self.check_equal(f"{label} NWI pop sum == total", json_data["population"], nwi_pop_sum)

        # Weighted avg NWI
        weighted_sum = sum(to_float(r["nwi_scaled_10"]) * to_float(r["b02001_001e"]) for r in rows)
        total_pop = self.sum_col(rows, "b02001_001e")
        expected_nwi = round(weighted_sum / total_pop, 2) if total_pop > 0 else 0
        self.check_equal(f"{label} avg_nwi", expected_nwi, json_data["avg_nwi"], tolerance=NWI_TOLERANCE)

        # NWI level populations
        for nwi_level in range(4):
            level_rows = [r for r in rows if int(to_float(r["nwi"])) == nwi_level]
            expected_level_pop = round(self.sum_col(level_rows, "b02001_001e"))
            actual_level_pop = json_data["by_nwi"].get(str(nwi_level), {}).get("population", 0)
            self.check_equal(f"{label} NWI-{nwi_level} pop", expected_level_pop, actual_level_pop)

            if skip_demographics:
                continue

            # Demographics per NWI level
            demos = json_data["by_nwi"].get(str(nwi_level), {}).get("demographics", {})

            # Race
            if "race" in demos:
                for key, col in RACE_COLS.items():
                    expected = round(self.sum_col(level_rows, col))
                    actual = demos["race"].get(key, 0)
                    self.check_equal(f"{label} NWI-{nwi_level} race.{key}", expected, actual)

            # Income
            if "income" in demos:
                for key, cols in INCOME_BUCKETS.items():
                    expected = round(self.sum_cols(level_rows, cols))
                    actual = demos["income"].get(key, 0)
                    self.check_equal(f"{label} NWI-{nwi_level} income.{key}", expected, actual)

            # Homeownership
            if "homeownership" in demos:
                for key, col in HOMEOWNERSHIP_COLS.items():
                    expected = round(self.sum_col(level_rows, col))
                    actual = demos["homeownership"].get(key, 0)
                    self.check_equal(f"{label} NWI-{nwi_level} homeownership.{key}", expected, actual)

            # Transportation (direct columns)
            if "transportation" in demos:
                for key, col in TRANSPORT_COLS.items():
                    expected = round(self.sum_col(level_rows, col))
                    actual = demos["transportation"].get(key, 0)
                    self.check_equal(f"{label} NWI-{nwi_level} transport.{key}", expected, actual)

                # Carpool (computed)
                expected_carpool = round(self.compute_carpool(level_rows))
                actual_carpool = demos["transportation"].get("carpool", 0)
                self.check_equal(f"{label} NWI-{nwi_level} transport.carpool", expected_carpool, actual_carpool)

            # Ethnicity (computed)
            if "ethnicity" in demos:
                expected_hispanic = round(self.compute_hispanic(level_rows))
                actual_hispanic = demos["ethnicity"].get("hispanic", 0)
                self.check_equal(f"{label} NWI-{nwi_level} ethnicity.hispanic", expected_hispanic, actual_hispanic)

                expected_non_hisp = round(self.sum_col(level_rows, "b03002_002e"))
                actual_non_hisp = demos["ethnicity"].get("non_hispanic", 0)
                self.check_equal(f"{label} NWI-{nwi_level} ethnicity.non_hispanic", expected_non_hisp, actual_non_hisp)

            # Age
            if "age" in demos:
                for key, col in AGE_COLS.items():
                    expected = round(self.sum_col(level_rows, col))
                    actual = demos["age"].get(key, 0)
                    self.check_equal(f"{label} NWI-{nwi_level} age.{key}", expected, actual)

            # Education
            if "education" in demos:
                for key, col in EDUCATION_COLS.items():
                    expected = round(self.sum_col(level_rows, col))
                    actual = demos["education"].get(key, 0)
                    self.check_equal(f"{label} NWI-{nwi_level} education.{key}", expected, actual)

    def verify_national(self):
        print("\n=== National ===")
        with open(DATA / "national.json") as f:
            national = json.load(f)
        rows = self.query_block_groups("1=1")
        print(f"  {len(rows):,} block groups")
        self.verify_jurisdiction("National", national, rows)

    def verify_states(self):
        print("\n=== States ===")
        with open(DATA / "states.json") as f:
            states = json.load(f)
        print(f"  {len(states)} states in JSON")

        for fips, state in states.items():
            rows = self.query_block_groups("substr(geoid10, 1, 2) = ?", (fips,))
            if not rows:
                self.error(f"State {fips} ({state['name']}): no block groups found in DB")
                continue
            self.verify_jurisdiction(f"State {fips} ({state['name']})", state, rows)

    def verify_counties(self):
        print("\n=== Counties ===")
        with open(DATA / "counties.json") as f:
            counties = json.load(f)
        print(f"  {len(counties)} counties in JSON")

        for fips, county in counties.items():
            if fips in PATCHED_COUNTIES:
                # Just verify they exist with documented populations
                self.checks += 1
                print(f"  SKIP (patched): {fips} ({county['name']}) — pop {county['population']:,}")
                continue

            rows = self.query_block_groups("substr(geoid10, 1, 5) = ?", (fips,))
            if not rows:
                self.error(f"County {fips} ({county['name']}): no block groups found in DB")
                continue
            self.verify_jurisdiction(f"County {fips} ({county['name']})", county, rows)

    def verify_cities(self):
        """Verify cities — area-weighted, so check internal consistency only."""
        print("\n=== Cities (area-weighted) ===")
        with open(DATA / "cities.json") as f:
            cities = json.load(f)
        print(f"  {len(cities)} cities in JSON")

        for key, city in cities.items():
            parts = key.rsplit("|", 1)
            if len(parts) != 2:
                self.error(f"City key format invalid: {key}")
                continue

            # Internal consistency: NWI level pops sum to total
            nwi_pop_sum = sum(city["by_nwi"].get(str(i), {}).get("population", 0) for i in range(4))
            self.check_equal(f"City {key} NWI pop sum == total", city["population"], nwi_pop_sum)

            # Check all demographic categories present at each NWI level
            for nwi_level in range(4):
                demos = city["by_nwi"].get(str(nwi_level), {}).get("demographics", {})
                for cat in ("race", "income", "homeownership", "transportation", "ethnicity", "age", "education"):
                    if cat not in demos:
                        self.error(f"City {key} NWI-{nwi_level} missing {cat}")
                    else:
                        self.ok()

            # Population should be positive
            if city["population"] <= 0:
                self.error(f"City {key} has non-positive population: {city['population']}")
            else:
                self.ok()

        # Cross-check: total city pop per state should not exceed state pop
        with open(DATA / "states.json") as f:
            states = json.load(f)
        city_pop_by_state = {}
        for key, city in cities.items():
            state_fips = key.rsplit("|", 1)[1]
            city_pop_by_state[state_fips] = city_pop_by_state.get(state_fips, 0) + city["population"]

        for state_fips, city_pop in city_pop_by_state.items():
            state_pop = states.get(state_fips, {}).get("population", 0)
            if city_pop > state_pop * 1.01:  # 1% tolerance for rounding
                self.warn(f"State {state_fips}: city pop sum {city_pop:,} exceeds state pop {state_pop:,} by {city_pop - state_pop:,}")
            else:
                self.ok()

    def verify_csas(self):
        print("\n=== CSAs ===")
        with open(DATA / "csas.json") as f:
            csas = json.load(f)
        print(f"  {len(csas)} CSAs in JSON")

        for key, csa in csas.items():
            rows = self.query_block_groups("csa_name = ?", (key,))
            if not rows:
                self.error(f"CSA '{key}': no block groups found in DB")
                continue
            self.verify_jurisdiction(f"CSA '{key}'", csa, rows)

    def verify_internal_consistency(self):
        """Cross-level checks."""
        print("\n=== Cross-level consistency ===")

        # National pop should equal sum of all state pops
        with open(DATA / "national.json") as f:
            national = json.load(f)
        with open(DATA / "states.json") as f:
            states = json.load(f)

        state_pop_sum = sum(s["population"] for s in states.values())
        self.check_equal("National pop == sum(state pops)", national["population"], state_pop_sum)

        # Per-state: state pop should equal sum of its county pops
        with open(DATA / "counties.json") as f:
            counties = json.load(f)

        for state_fips, state in states.items():
            county_pop = sum(
                c["population"] for fips, c in counties.items()
                if fips[:2] == state_fips
            )
            # Counties may not cover all block groups (some have null county_name)
            # so this is a warning, not an error
            if county_pop != state["population"]:
                diff = state["population"] - county_pop
                pct = abs(diff) / state["population"] * 100 if state["population"] > 0 else 0
                if pct > 1:
                    self.warn(f"State {state_fips} ({state['name']}): county pop sum {county_pop:,} vs state pop {state['population']:,} (diff {diff:+,}, {pct:.1f}%)")
                self.ok()
            else:
                self.ok()

    def verify_patched_counties(self):
        """Verify the 3 manually patched counties exist with expected populations."""
        print("\n=== Patched counties ===")
        with open(DATA / "counties.json") as f:
            counties = json.load(f)

        expected = {
            "46113": ("Oglala Lakota County, South Dakota", 14335),
            "02270": ("Kusilvak Census Area, Alaska", 8250),
            "51515": ("Bedford city, Virginia", 6449),
        }

        for fips, (name, pop) in expected.items():
            if fips not in counties:
                self.error(f"Patched county {fips} ({name}) missing from JSON")
                continue
            self.check_equal(f"Patched {fips} population", pop, counties[fips]["population"])
            # Verify internal consistency
            nwi_sum = sum(counties[fips]["by_nwi"].get(str(i), {}).get("population", 0) for i in range(4))
            self.check_equal(f"Patched {fips} NWI pop sum", pop, nwi_sum)

    def run(self):
        print(f"Database: {DB}")
        print(f"Data dir: {DATA}")

        if not DB.exists():
            print(f"ERROR: Database not found at {DB}")
            sys.exit(1)

        self.verify_national()
        self.verify_states()
        self.verify_counties()
        self.verify_cities()
        self.verify_csas()
        self.verify_internal_consistency()
        self.verify_patched_counties()

        self.conn.close()

        print(f"\n{'=' * 60}")
        print(f"Checks: {self.checks:,}")
        print(f"Errors: {len(self.errors)}")
        print(f"Warnings: {len(self.warnings)}")

        if self.errors:
            print(f"\n--- ERRORS ---")
            for e in self.errors:
                print(f"  {e}")
            print(f"\nFAILED — {len(self.errors)} error(s)")
            sys.exit(1)
        else:
            print("\nAll checks passed.")
            sys.exit(0)


if __name__ == "__main__":
    Verifier().run()
