#!/bin/bash
# Download Census 2010 block group cartographic boundaries (500k simplified)
# and build PMTiles with NWI scores embedded
set -e
cd "$(dirname "$0")/boundaries"
mkdir -p bg_raw

FIPS="01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 72"

echo "Downloading block group boundaries..."
for f in $FIPS; do
  if [ ! -f "bg_raw/gz_2010_${f}_150_00_500k.shp" ]; then
    echo "  $f..."
    curl -sL "https://www2.census.gov/geo/tiger/GENZ2010/gz_2010_${f}_150_00_500k.zip" -o "bg_raw/${f}.zip"
    if file "bg_raw/${f}.zip" | grep -q "Zip archive"; then
      unzip -qo "bg_raw/${f}.zip" -d bg_raw/
      rm -f "bg_raw/${f}.zip"
    else
      echo "    FAILED: $f"
      rm -f "bg_raw/${f}.zip"
    fi
  fi
done

echo "Counting shapefiles..."
ls bg_raw/gz_2010_*_150_00_500k.shp | wc -l
echo "Done downloading."
