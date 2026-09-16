#!/bin/bash
# Download the full-resolution TIGER 2010 block group boundaries, per state, for the area
# apportionment. Full-resolution (not the 500k cartographic files in bg_raw/) so that shared
# edges with the TIGER 2010 places coincide and the overlay produces no slivers.
# Skip-if-present, like build_blockgroups.sh.
set -e
cd "$(dirname "$0")/boundaries"
mkdir -p bg_full

FIPS="01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 72"

echo "Downloading full-resolution block group boundaries..."
for f in $FIPS; do
  if [ ! -f "bg_full/tl_2010_${f}_bg10.shp" ]; then
    echo "  $f"
    curl -sL "https://www2.census.gov/geo/tiger/TIGER2010/BG/2010/tl_2010_${f}_bg10.zip" -o "bg_full/${f}.zip"
    if file "bg_full/${f}.zip" | grep -q "Zip archive"; then
      unzip -qo "bg_full/${f}.zip" -d bg_full/
    else
      echo "    FAILED: $f"
    fi
    rm -f "bg_full/${f}.zip"
  fi
done
echo "Block group shapefiles: $(ls bg_full/tl_2010_*_bg10.shp | wc -l)"
echo "Done."
