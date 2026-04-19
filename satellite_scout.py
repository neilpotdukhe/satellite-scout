#!/usr/bin/env python3
"""
satellite_scout.py — Find locations via OpenStreetMap, fetch satellite imagery, analyze with AI vision.

Usage:
    python satellite_scout.py "tennis courts in Seattle"
    python satellite_scout.py --type "tennis court" --area "Seattle" --zoom 19

Data sources:
    - Locations: Overpass API (OpenStreetMap) — free, no key
    - Imagery: Esri World Imagery — free, no key
    - Analysis: Claude API (optional, needs ANTHROPIC_API_KEY)
"""

import requests
import math
import json
import os
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- OSM Tag Mappings ---
TAG_MAP = {
    "tennis court":     '["leisure"="pitch"]["sport"="tennis"]',
    "basketball court": '["leisure"="pitch"]["sport"="basketball"]',
    "soccer field":     '["leisure"="pitch"]["sport"="soccer"]',
    "baseball field":   '["leisure"="pitch"]["sport"="baseball"]',
    "parking lot":      '["amenity"="parking"]',
    "park":             '["leisure"="park"]',
    "swimming pool":    '["leisure"="swimming_pool"]',
    "playground":       '["leisure"="playground"]',
    "school":           '["amenity"="school"]',
    "gas station":      '["amenity"="fuel"]',
    "hospital":         '["amenity"="hospital"]',
    "library":          '["amenity"="library"]',
    "golf course":      '["leisure"="golf_course"]',
    "skate park":       '["leisure"="pitch"]["sport"="skateboard"]',
    "dog park":         '["leisure"="dog_park"]',
}

ZOOM_DEFAULTS = {
    "tennis court": 19,
    "basketball court": 19,
    "parking lot": 18,
    "park": 17,
    "school": 18,
    "golf course": 16,
}


def find_locations(place_type: str, area: str) -> list[dict]:
    """Find locations via Overpass API (OpenStreetMap)."""
    tags = TAG_MAP.get(place_type.lower())
    if not tags:
        print(f"[!] Unknown place type '{place_type}'. Known types: {', '.join(TAG_MAP.keys())}")
        sys.exit(1)

    query = f"""[out:json][timeout:60];
area["name"="{area}"]->.searchArea;
(
  node{tags}(area.searchArea);
  way{tags}(area.searchArea);
  relation{tags}(area.searchArea);
);
out center body;
"""
    print(f"[*] Querying Overpass API for {place_type}s in {area}...")
    resp = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    elements = data.get("elements", [])

    locations = []
    for el in elements:
        # Ways/relations have center coords, nodes have direct coords
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lng = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lng is None:
            continue

        name = el.get("tags", {}).get("name", f"Unnamed ({el['type']}/{el['id']})")
        locations.append({
            "id": el["id"],
            "name": name,
            "lat": lat,
            "lng": lng,
            "tags": el.get("tags", {}),
            "osm_type": el["type"],
        })

    print(f"[*] Found {len(locations)} {place_type}(s) in {area}")
    return locations


def get_satellite_image(lat: float, lng: float, zoom: int = 19, size: int = 800) -> bytes:
    """Fetch satellite image from Esri World Imagery export endpoint."""
    # Calculate bounding box from center point + zoom
    meters_per_pixel = 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)
    half_span_m = (size * meters_per_pixel) / 2
    # Convert meters to degrees (approximate)
    half_deg_lng = half_span_m / (111320 * math.cos(math.radians(lat)))
    half_deg_lat = half_span_m / 110540

    bbox = f"{lng - half_deg_lng},{lat - half_deg_lat},{lng + half_deg_lng},{lat + half_deg_lat}"

    url = (
        "https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export"
        f"?bbox={bbox}&bboxSR=4326&size={size},{size}&format=jpg&f=image"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def download_images(locations: list[dict], output_dir: Path, zoom: int = 19, max_workers: int = 5) -> list[dict]:
    """Download satellite images for all locations. Returns updated locations with image paths."""
    output_dir.mkdir(parents=True, exist_ok=True)

    def fetch_one(loc):
        safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in loc["name"])[:60].strip()
        filename = f"{loc['id']}_{safe_name}.jpg"
        filepath = output_dir / filename

        if filepath.exists():
            print(f"  [skip] {loc['name']} (already downloaded)")
            loc["image_path"] = str(filepath)
            return loc

        try:
            img_bytes = get_satellite_image(loc["lat"], loc["lng"], zoom=zoom)
            filepath.write_bytes(img_bytes)
            loc["image_path"] = str(filepath)
            print(f"  [ok]   {loc['name']}")
        except Exception as e:
            print(f"  [err]  {loc['name']}: {e}")
            loc["image_path"] = None
        return loc

    print(f"\n[*] Downloading satellite images (zoom={zoom})...")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(fetch_one, loc): loc for loc in locations}
        results = []
        for future in as_completed(futures):
            results.append(future.result())

    # Sort back to original order
    results.sort(key=lambda x: x["id"])
    return results


def generate_report(locations: list[dict], output_dir: Path):
    """Save a JSON report of all locations and their images."""
    report_path = output_dir / "report.json"
    report = {
        "total": len(locations),
        "with_images": sum(1 for l in locations if l.get("image_path")),
        "locations": locations,
    }
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\n[*] Report saved to {report_path}")
    return report_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Satellite Scout — spatial queries with aerial imagery")
    parser.add_argument("query", nargs="?", help="Natural language query (e.g. 'tennis courts in Seattle')")
    parser.add_argument("--type", help="Location type (e.g. 'tennis court')")
    parser.add_argument("--area", help="Geographic area (e.g. 'Seattle')")
    parser.add_argument("--zoom", type=int, help="Satellite image zoom level (default: auto)")
    parser.add_argument("--output", default="images", help="Output directory for images")
    args = parser.parse_args()

    # Parse query or use explicit args
    if args.query and not (args.type and args.area):
        # Simple parsing: "tennis courts in Seattle" → type="tennis court", area="Seattle"
        q = args.query.lower()
        if " in " in q:
            type_part, area_part = q.rsplit(" in ", 1)
            # Depluralize naive
            place_type = type_part.rstrip("s") if type_part.endswith("s") and not type_part.endswith("ss") else type_part
            area = area_part.strip().title()
        else:
            print("[!] Query format: '<type> in <area>' (e.g. 'tennis courts in Seattle')")
            sys.exit(1)
    elif args.type and args.area:
        place_type = args.type.lower()
        area = args.area
    else:
        parser.print_help()
        sys.exit(1)

    zoom = args.zoom or ZOOM_DEFAULTS.get(place_type, 18)
    output_dir = Path(args.output)

    # Step 1: Find locations
    locations = find_locations(place_type, area)
    if not locations:
        print("[!] No locations found.")
        sys.exit(0)

    # Step 2: Download satellite images
    locations = download_images(locations, output_dir, zoom=zoom)

    # Step 3: Generate report
    generate_report(locations, output_dir)

    # Summary
    print(f"\n{'='*60}")
    print(f"  Found {len(locations)} locations")
    print(f"  Images saved to: {output_dir}/")
    print(f"  Report: {output_dir}/report.json")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
