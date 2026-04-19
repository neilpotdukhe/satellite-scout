"""Enrich the basketball cache with beexploring quotes via forward geocoding.

For each beexploring park, forward-geocode the name (Nominatim search) to get
its coords, then match the nearest OSM court in the cache within 300m and
attach the quote + source URL.
"""
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
QUERY = "basketball courts in Seattle"
QUERY_HASH = hashlib.md5(QUERY.lower().encode()).hexdigest()[:12]
CACHE = ROOT / f"cache/queries/{QUERY_HASH}.json"

beexploring = json.loads((ROOT / "data/basketball_beexploring.json").read_text())
cache_data = json.loads(CACHE.read_text())
results = cache_data["results"]


def haversine_m(a_lat, a_lng, b_lat, b_lng):
    R = 6371000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


GEO_CACHE = ROOT / "cache/web/nominatim_search"
GEO_CACHE.mkdir(parents=True, exist_ok=True)


def nominatim_search(q):
    key = hashlib.md5(q.encode()).hexdigest()[:12] + ".json"
    cp = GEO_CACHE / key
    if cp.exists():
        return json.loads(cp.read_text())
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": q, "format": "json", "limit": 3, "countrycodes": "us"}
    r = requests.get(url, params=params, headers={"User-Agent": "satellite-scout/1.0 (neil@localhost)"}, timeout=15)
    data = r.json() if r.status_code == 200 else []
    cp.write_text(json.dumps(data))
    time.sleep(1.1)
    return data


def best_match_for_park(be_name):
    # Strip "Basketball Court(s)" suffix, add "Seattle"
    park_q = be_name.replace("Basketball Courts", "").replace("Basketball Court", "").strip()
    q = f"{park_q}, Seattle, WA"
    hits = nominatim_search(q)
    for h in hits:
        lat, lng = float(h["lat"]), float(h["lon"])
        if 47.4 < lat < 47.8 and -122.5 < lng < -122.2:
            return lat, lng, h.get("display_name", "")
    return None


matched = 0
for be in beexploring:
    m = best_match_for_park(be["name"])
    if not m:
        print(f"[-] no geocode: {be['name']}")
        continue
    blat, blng, disp = m
    # Find nearest OSM court within 400m
    nearest = None
    nearest_d = 1e9
    for r in results:
        d = haversine_m(blat, blng, r["lat"], r["lng"])
        if d < nearest_d:
            nearest_d = d
            nearest = r
    if nearest and nearest_d < 500:
        # Attach quote
        park_core = be["name"].replace("Basketball Courts", "").replace("Basketball Court", "").strip()
        nearest["name"] = park_core + (" Basketball Court" if "court" not in park_core.lower() else "")
        nearest["backboard_quote"] = be["quote"]
        nearest["features_quote"] = f"Type: {be.get('type', 'full')} | Hoops: {be.get('hoops', 'n/a')} | Neighborhood: {be.get('neighborhood', '')}"
        nearest["summary"] = f"{nearest['name']} in the {be.get('neighborhood', 'Seattle')} area. {be['quote']}"
        nearest["source_url"] = be["source_url"]
        matched += 1
        print(f"[+] {be['name']} → {nearest['id']} ({nearest_d:.0f}m)")
    else:
        print(f"[-] no nearby OSM court for {be['name']} (nearest={nearest_d:.0f}m)")

cache_data["stats"] = {"yes": len(results), "no": 0, "unclear": 0}
CACHE.write_text(json.dumps(cache_data, indent=2))
print(f"\n[✓] matched {matched}/{len(beexploring)} beexploring quotes")
print(f"[✓] rewrote {CACHE}")
