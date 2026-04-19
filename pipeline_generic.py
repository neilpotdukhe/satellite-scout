"""Generic query pipeline — handles arbitrary spatial queries beyond Seattle tennis.

Step 3 of the build: supports any location type + area + target feature.
Uses caching aggressively since each query can trigger dozens of fetches.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Optional

import requests

from cache import web_cache, image_cache, IMAGE_CACHE


OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def _overpass_query(place_type_tags: list[str], area: str) -> list[dict]:
    """Find OSM elements matching the given tags within the named area."""
    tags_block = ""
    for t in place_type_tags:
        tags_block += f"  way{t}(area.searchArea);\n"
        tags_block += f"  node{t}(area.searchArea);\n"

    query = f"""[out:json][timeout:60];
area["name"="{area}"]->.searchArea;
(
{tags_block}
);
out center body;
"""

    cache_key = f"overpass_{area}_{hash(tuple(place_type_tags))}"
    cached = web_cache.get(cache_key)
    if cached:
        return cached

    resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    elements = data.get("elements", [])

    locations = []
    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lng = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lng is None:
            continue
        locations.append({
            "id": f"{el['type']}_{el['id']}",
            "name": el.get("tags", {}).get("name", f"Unnamed {el['type']}/{el['id']}"),
            "lat": lat,
            "lng": lng,
            "tags": el.get("tags", {}),
        })

    web_cache.set(cache_key, locations)
    return locations


def _cluster_nearby(locs: list[dict], radius_m: float = 100) -> list[dict]:
    """Cluster locations within radius_m to avoid duplicate facility entries."""
    def haversine(a, b):
        R = 6371000
        dlat = math.radians(b["lat"] - a["lat"])
        dlon = math.radians(b["lng"] - a["lng"])
        x = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(a["lat"])) * math.cos(math.radians(b["lat"])) *
             math.sin(dlon / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(x))

    used = set()
    clusters = []
    for i, loc in enumerate(locs):
        if i in used:
            continue
        cluster = [loc]
        used.add(i)
        for j, other in enumerate(locs):
            if j in used:
                continue
            if haversine(loc, other) < radius_m:
                cluster.append(other)
                used.add(j)
        # Pick a representative — prefer named
        named = [c for c in cluster if not c["name"].startswith("Unnamed")]
        rep = named[0] if named else cluster[0]
        rep = dict(rep)
        rep["member_count"] = len(cluster)
        clusters.append(rep)
    return clusters


def _fetch_satellite(lat: float, lng: float, zoom: int = 20, size: int = 800) -> str | None:
    """Fetch a Google Maps satellite tile composite. Returns a path under /static/cache."""
    from PIL import Image
    from io import BytesIO

    key = f"{lat:.6f}_{lng:.6f}_z{zoom}_s{size}"
    cache_path = IMAGE_CACHE / f"{key}.jpg"
    if cache_path.exists():
        return f"/static/cache-images/{key}.jpg"

    def lat_lng_to_tile(lat, lng, zoom):
        n = 2 ** zoom
        x = (lng + 180) / 360 * n
        lat_rad = math.radians(lat)
        y = (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n
        return x, y

    cx_f, cy_f = lat_lng_to_tile(lat, lng, zoom)
    cx, cy = int(cx_f), int(cy_f)
    frac_x = (cx_f - cx) * 256
    frac_y = (cy_f - cy) * 256

    tiles_around = 3
    imgs = {}
    for dx in range(-tiles_around, tiles_around + 1):
        for dy in range(-tiles_around, tiles_around + 1):
            tx, ty = cx + dx, cy + dy
            url = f"https://mt1.google.com/vt/lyrs=s&x={tx}&y={ty}&z={zoom}"
            try:
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                if r.status_code == 200 and len(r.content) > 1000:
                    imgs[(dx, dy)] = Image.open(BytesIO(r.content))
            except Exception:
                pass
            time.sleep(0.02)

    if len(imgs) < 10:
        return None

    grid_size = tiles_around * 2 + 1
    ts = 256
    composite = Image.new("RGB", (grid_size * ts, grid_size * ts))
    for (dx, dy), img in imgs.items():
        composite.paste(img, ((dx + tiles_around) * ts, (dy + tiles_around) * ts))

    center_x = tiles_around * ts + frac_x
    center_y = tiles_around * ts + frac_y
    half = size // 2
    cropped = composite.crop((int(center_x - half), int(center_y - half),
                              int(center_x - half) + size, int(center_y - half) + size))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(cache_path, quality=90)
    return f"/static/cache-images/{key}.jpg"


def run_generic_query(query: str, parsed: dict) -> dict:
    """Full generic pipeline for arbitrary spatial queries.

    NOTE: Step 3 stub — returns a minimal result with locations found + satellite images.
    Classification for generic queries requires more sophisticated source gathering
    (web search per location) which is scaffolded but not fully wired up here.
    """
    location_type = parsed.get("location_type", "tennis court")
    area = parsed.get("area", "Seattle")
    target_feature = parsed.get("target_feature", "feature")
    tags = parsed.get("osm_tags", ['["leisure"="pitch"]'])

    try:
        locations = _overpass_query(tags, area)
    except Exception as e:
        return {
            "query": query,
            "error": f"Overpass query failed: {e}",
            "location_type": location_type,
            "area": area,
            "target_feature": target_feature,
            "stats": {"yes": 0, "no": 0, "unclear": 0},
            "total": 0,
            "results": [],
        }

    clusters = _cluster_nearby(locations)

    # For each cluster, fetch satellite image (capped at 20 to keep demo fast)
    results = []
    for c in clusters[:20]:
        img_path = _fetch_satellite(c["lat"], c["lng"], zoom=19, size=800)
        results.append({
            "id": str(c["id"]),
            "name": c["name"],
            "address": "",
            "lat": c["lat"],
            "lng": c["lng"],
            "courts": c.get("member_count", 1),
            "image": img_path,
            "classification": "unclear",
            "summary": f"Found via Overpass API in {area}. Classification for this query "
                       f"requires web source gathering (scaffolded but not wired up).",
            "backboard_quote": None,
            "features_quote": None,
            "source_url": None,
        })

    return {
        "query": query,
        "target_feature": target_feature,
        "location_type": location_type,
        "area": area,
        "stats": {"yes": 0, "no": 0, "unclear": len(results)},
        "total": len(results),
        "results": results,
        "note": "Generic pipeline returns locations but cannot authoritatively classify without "
                "targeted web source gathering. For the canonical query 'tennis courts in Seattle "
                "with hitting walls', Satellite Scout has pre-gathered First Serve Seattle sources.",
    }
