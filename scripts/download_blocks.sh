#!/bin/bash
# Download the 2010 block inputs for place-by-county apportionment:
#   - Block Assignment Files (block -> incorporated place / CDP), all states + DC + PR
#   - TIGER 2010 block shapefiles with 2010 population and housing (pophu), 50 states + DC
#   - PL 94-171 redistricting summary file for Puerto Rico (no pophu file exists for PR)
# Skip-if-present, like build_blockgroups.sh.
set -e
cd "$(dirname "$0")/boundaries"
mkdir -p baf blocks_pophu

# FIPS -> USPS abbreviation, read off the Census BAF directory listing (the abbreviation is part of the filename)
BAF="01_AL 02_AK 04_AZ 05_AR 06_CA 08_CO 09_CT 10_DE 11_DC 12_FL 13_GA 15_HI 16_ID 17_IL 18_IN 19_IA 20_KS 21_KY 22_LA 23_ME 24_MD 25_MA 26_MI 27_MN 28_MS 29_MO 30_MT 31_NE 32_NV 33_NH 34_NJ 35_NM 36_NY 37_NC 38_ND 39_OH 40_OK 41_OR 42_PA 44_RI 45_SC 46_SD 47_TN 48_TX 49_UT 50_VT 51_VA 53_WA 54_WV 55_WI 56_WY 72_PR"

echo "Block Assignment Files (INCPLACE_CDP only)..."
for s in $BAF; do
  f="baf/BlockAssign_ST${s}_INCPLACE_CDP.txt"
  if [ ! -f "$f" ]; then
    echo "  $s"
    curl -sL "https://www2.census.gov/geo/docs/maps-data/data/baf/BlockAssign_ST${s}.zip" -o "baf/${s}.zip"
    if file "baf/${s}.zip" | grep -q "Zip archive"; then
      unzip -qo "baf/${s}.zip" "BlockAssign_ST${s}_INCPLACE_CDP.txt" -d baf/
    else
      echo "    FAILED: $s"
    fi
    rm -f "baf/${s}.zip"
  fi
done

echo "Puerto Rico PL 94-171 (block population + housing)..."
if [ ! -f "blocks_pophu/prgeo2010.pl" ]; then
  curl -sL "https://www2.census.gov/census_2010/01-Redistricting_File--PL_94-171/Puerto_Rico/pr2010.pl.zip" -o blocks_pophu/pr2010.pl.zip
  if file blocks_pophu/pr2010.pl.zip | grep -q "Zip archive"; then
    unzip -qo blocks_pophu/pr2010.pl.zip -d blocks_pophu/
  else
    echo "    FAILED: PR PL file"
  fi
  rm -f blocks_pophu/pr2010.pl.zip
fi

echo "TIGER 2010 blocks with population and housing (pophu)..."
for s in $BAF; do
  f="${s%_*}"
  [ "$f" = "72" ] && continue   # no pophu file for Puerto Rico
  if [ ! -f "blocks_pophu/tabblock2010_${f}_pophu.shp" ]; then
    echo "  $f"
    curl -sL "https://www2.census.gov/geo/tiger/TIGER2010BLKPOPHU/tabblock2010_${f}_pophu.zip" -o "blocks_pophu/${f}.zip"
    if file "blocks_pophu/${f}.zip" | grep -q "Zip archive"; then
      unzip -qo "blocks_pophu/${f}.zip" -d blocks_pophu/
    else
      echo "    FAILED: $f"
    fi
    rm -f "blocks_pophu/${f}.zip"
  fi
done

echo "BAF files: $(ls baf/*_INCPLACE_CDP.txt | wc -l)   pophu shapefiles: $(ls blocks_pophu/*_pophu.shp | wc -l)"
echo "Done."
