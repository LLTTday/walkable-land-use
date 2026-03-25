#!/bin/bash
# Download all TIGER 2010 place boundaries, merge, and convert to GeoJSON
set -e
cd "$(dirname "$0")/boundaries"
mkdir -p places_raw

FIPS="01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 72"

echo "Downloading place boundaries..."
for f in $FIPS; do
  if [ ! -f "places_raw/tl_2010_${f}_place10.shp" ]; then
    curl -sL "https://www2.census.gov/geo/tiger/TIGER2010/PLACE/2010/tl_2010_${f}_place10.zip" -o "places_raw/${f}.zip"
    unzip -qo "places_raw/${f}.zip" -d places_raw/
    rm -f "places_raw/${f}.zip"
  fi
done
echo "Downloaded all state place files."

echo "Merging into single GeoJSON..."
# Use ogr2ogr to merge all shapefiles
first=1
for f in places_raw/tl_2010_*_place10.shp; do
  if [ $first -eq 1 ]; then
    ogr2ogr -f GeoJSON -t_srs EPSG:4326 places_all.geojson "$f"
    first=0
  else
    ogr2ogr -f GeoJSON -t_srs EPSG:4326 -append places_all.geojson "$f"
  fi
done

echo "Done. places_all.geojson created."
ls -lh places_all.geojson
