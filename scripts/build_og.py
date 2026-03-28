#!/usr/bin/env python3
"""
Build OG share card assets:
1. Generic OG image (1200x630 PNG) with America Walks branding
2. Jurisdiction index JSON (slug → name, score, pop, level) for Cloudflare Functions
"""

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PUBLIC = Path(__file__).parent.parent / "public"
FUNCTIONS = Path(__file__).parent.parent / "functions"
DATA = PUBLIC / "data"

# State FIPS → abbreviation (matches main.ts FIPS_TO_ABBREV)
FIPS_TO_ABBREV = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA',
    '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL',
    '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN',
    '19': 'IA', '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME',
    '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS',
    '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
    '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND',
    '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI',
    '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT',
    '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI',
    '56': 'WY',
}


def to_slug(text):
    """Match main.ts toSlug() behavior."""
    s = text.lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'\s+', '-', s.strip())
    s = s.strip('-')
    return s


def format_pop(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def build_jurisdiction_index():
    """Build a slim slug → metadata index for OG tag generation."""
    index = {}
    seen_slugs = {}  # track collisions

    def add_entry(level, slug, name, score, pop, data):
        # Handle slug collisions (same as main.ts buildSlugMap)
        key = f"{level}/{slug}"
        if key in seen_slugs:
            seen_slugs[key] += 1
            slug = f"{slug}-{seen_slugs[key]}"
            key = f"{level}/{slug}"
        else:
            seen_slugs[key] = 1

        above = 0
        if pop > 0 and 'by_nwi' in data:
            above_pop = (data.get('by_nwi', {}).get('2', {}).get('population', 0) +
                         data.get('by_nwi', {}).get('3', {}).get('population', 0))
            above = round(above_pop / pop * 100)

        index[key] = {
            "name": name,
            "score": score,
            "pop": pop,
            "above": above,
        }

    # States
    with open(DATA / "states.json") as f:
        states = json.load(f)
    for fips, entry in states.items():
        name = entry["name"]
        slug = to_slug(name)
        add_entry("state", slug, name, entry["avg_nwi"], entry["population"], entry)

    # Counties
    with open(DATA / "counties.json") as f:
        counties = json.load(f)
    for fips, entry in counties.items():
        name = entry["name"]
        state_fips = fips[:2]
        abbrev = FIPS_TO_ABBREV.get(state_fips, '')
        # "Dallas County, Texas" → "dallas-county-tx"
        parts = name.rsplit(", ", 1)
        if len(parts) == 2 and abbrev:
            slug = to_slug(f"{parts[0]} {abbrev}")
        else:
            slug = to_slug(name)
        add_entry("county", slug, name, entry["avg_nwi"], entry["population"], entry)

    # Cities
    with open(DATA / "cities.json") as f:
        cities = json.load(f)
    for key, entry in cities.items():
        name = entry["name"]
        # "Portland, Oregon" → "portland-or"
        parts = name.rsplit(", ", 1)
        if len(parts) == 2:
            city_name = parts[0]
            # Find state abbreviation from the pipe key
            state_fips = key.rsplit("|", 1)[-1] if "|" in key else ""
            abbrev = FIPS_TO_ABBREV.get(state_fips, '')
            if abbrev:
                slug = to_slug(f"{city_name} {abbrev}")
            else:
                slug = to_slug(name)
        else:
            slug = to_slug(name)
        add_entry("city", slug, name, entry["avg_nwi"], entry["population"], entry)

    return index


def build_og_image():
    """Generate a 1200x630 OG image with America Walks branding."""
    W, H = 1200, 630

    # Colors from the walkability palette
    bg = (248, 247, 245)       # warm light gray
    dark = (42, 40, 38)        # near-black text
    green = (61, 107, 53)      # #3d6b35 — most walkable
    amber = (232, 168, 48)     # #e8a830
    red = (232, 72, 48)        # #e84830

    img = Image.new('RGB', (W, H), bg)
    draw = ImageDraw.Draw(img)

    # Draw walkability color bar at bottom
    bar_h = 8
    bar_y = H - bar_h
    colors = [red, amber, (200, 216, 104), (126, 191, 110), (90, 154, 74), green]
    seg_w = W // len(colors)
    for i, c in enumerate(colors):
        draw.rectangle([i * seg_w, bar_y, (i + 1) * seg_w, H], fill=c)

    # Try to use system fonts, fall back to default
    try:
        title_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 52)
        sub_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
    except (OSError, IOError):
        title_font = ImageFont.load_default()
        sub_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    # Overlay the AW logo
    logo_path = PUBLIC / "aw-logo.png"
    if logo_path.exists():
        logo = Image.open(logo_path).convert("RGBA")
        # Scale logo to fit nicely (max 300px wide)
        logo_max_w = 300
        if logo.width > logo_max_w:
            ratio = logo_max_w / logo.width
            logo = logo.resize((logo_max_w, int(logo.height * ratio)), Image.LANCZOS)
        logo_x = 80
        logo_y = 60
        img.paste(logo, (logo_x, logo_y), logo)

    # Title
    draw.text((80, 180), "Walkable Land Use", fill=dark, font=title_font)

    # Subtitle
    draw.text((80, 260), "Who lives in walkable places,", fill=(100, 96, 90), font=sub_font)
    draw.text((80, 300), "and who doesn't?", fill=(100, 96, 90), font=sub_font)

    # Stats line
    draw.text((80, 380), "Every state, county, and city in America", fill=(140, 136, 130), font=small_font)
    draw.text((80, 415), "Demographics  \u00b7  Walkability Index  \u00b7  Interactive Maps", fill=(140, 136, 130), font=small_font)

    # URL
    draw.text((80, 500), "americawalks.org", fill=green, font=sub_font)

    out = PUBLIC / "og-image.png"
    img.save(out, "PNG", optimize=True)
    print(f"OG image: {out} ({W}x{H})")


def main():
    print("Building OG image...")
    build_og_image()

    print("Building jurisdiction index...")
    index = build_jurisdiction_index()

    # Write to public/data for static serving — Functions fetch from same origin
    out = DATA / "og-index.json"
    with open(out, "w") as f:
        json.dump(index, f, separators=(",", ":"))
    size_kb = out.stat().st_size / 1024
    print(f"Jurisdiction index: {out} ({len(index)} entries, {size_kb:.0f}KB)")


if __name__ == "__main__":
    main()
