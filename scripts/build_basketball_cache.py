"""Build the Seattle basketball courts scout cache file.

Loads basketball_raw.json (all 136 OSM elements), clusters within 100m,
reverse-geocodes via Nominatim, fetches satellite tiles, matches
beexploring.com quotes where possible, and writes cache/queries/<hash>.json.

Previous version used basketball_selected.json which had dropped ~72 valid
courts (including the UW court) based on arbitrary criteria. This version
uses the full raw set with proximity-based clustering only.
"""
import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline_generic import _fetch_satellite  # noqa

QUERY = "basketball courts in Seattle"
QUERY_HASH = hashlib.md5(QUERY.lower().encode()).hexdigest()[:12]
print(f"[i] query hash: {QUERY_HASH}")

raw = json.loads((ROOT / "data/basketball_raw.json").read_text())
beexploring = json.loads((ROOT / "data/basketball_beexploring.json").read_text())
print(f"[i] raw={len(raw)}  beexploring={len(beexploring)}")


def haversine_m(a_lat, a_lng, b_lat, b_lng):
    R = 6371000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def cluster(elements, threshold_m=100):
    """Greedy proximity cluster. Picks the element with highest member_count as rep."""
    clusters = []
    used = [False] * len(elements)
    for i, a in enumerate(elements):
        if used[i]:
            continue
        group = [a]
        used[i] = True
        for j in range(i + 1, len(elements)):
            if used[j]:
                continue
            if haversine_m(a["lat"], a["lng"], elements[j]["lat"], elements[j]["lng"]) < threshold_m:
                group.append(elements[j])
                used[j] = True
        # Representative: the element with the highest member_count (most likely a real facility)
        rep = max(group, key=lambda e: e.get("member_count", 1))
        rep = dict(rep)
        rep["_cluster_size"] = len(group)
        clusters.append(rep)
    return clusters


clustered = cluster(raw, threshold_m=100)
print(f"[i] clustered {len(raw)} raw → {len(clustered)} unique locations")


NOMINATIM_CACHE = ROOT / "cache/web/nominatim"
NOMINATIM_CACHE.mkdir(parents=True, exist_ok=True)


def reverse_geocode(lat, lng):
    key = f"{lat:.5f}_{lng:.5f}.json"
    cp = NOMINATIM_CACHE / key
    if cp.exists():
        return json.loads(cp.read_text())
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {"lat": lat, "lon": lng, "format": "json", "zoom": 17, "addressdetails": 1}
    try:
        r = requests.get(url, params=params, headers={"User-Agent": "satellite-scout/1.0 (neil@localhost)"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            cp.write_text(json.dumps(data))
            time.sleep(1.1)  # Nominatim 1 req/s
            return data
    except Exception as e:
        print(f"[!] geocode fail {lat},{lng}: {e}")
    return {}


def slugify(s):
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "court"


def infer_name_from_geocode(geo):
    addr = geo.get("address", {}) or {}
    for key in ("leisure", "park", "school", "amenity", "attraction"):
        if key in addr and addr[key]:
            return addr[key]
    disp = geo.get("display_name", "") or ""
    if disp:
        first = disp.split(",")[0].strip()
        if first and not first.isdigit():
            return first
    nb = addr.get("neighbourhood") or addr.get("suburb") or addr.get("quarter")
    if nb:
        return f"{nb} Basketball Court"
    return "Seattle Basketball Court"


def short_address(geo):
    addr = geo.get("address", {}) or {}
    parts = []
    num = addr.get("house_number", "")
    road = addr.get("road", "")
    if road:
        parts.append(f"{num} {road}".strip())
    city = addr.get("city") or addr.get("town") or "Seattle"
    state = addr.get("state", "WA")
    postcode = addr.get("postcode", "")
    tail = f"{city}, {state}"
    if postcode:
        tail += f" {postcode}"
    parts.append(tail)
    return ", ".join(p for p in parts if p)


results = []
yes = no = unclear = 0

for i, osm in enumerate(clustered):
    lat, lng = osm["lat"], osm["lng"]
    tag_str = ",".join(f"{k}={v}" for k, v in osm.get("tags", {}).items() if k in ("sport", "hoops", "surface", "lit"))
    print(f"[{i+1}/{len(clustered)}] {osm['id']} {lat:.4f},{lng:.4f} [{tag_str}]")

    geo = reverse_geocode(lat, lng)
    osm_name = osm.get("name") or ""
    name = osm_name or infer_name_from_geocode(geo)
    address = short_address(geo)

    img = _fetch_satellite(lat, lng, zoom=19, size=800)
    if img is None:
        print(f"    [!] satellite fetch failed")

    tags = osm.get("tags", {})
    feat_parts = [f"OSM: leisure=pitch, sport=basketball"]
    if tags.get("hoops"):
        feat_parts.append(f"Hoops: {tags['hoops']}")
    if tags.get("surface"):
        feat_parts.append(f"Surface: {tags['surface']}")
    if tags.get("lit"):
        feat_parts.append(f"Lit: {tags['lit']}")
    features = " | ".join(feat_parts)

    summary = f"{name} — basketball court at {lat:.4f}, {lng:.4f}. Source: OpenStreetMap."
    if tags.get("hoops") or tags.get("surface") or tags.get("lit"):
        summary += f" Tags: {features}."

    cid = slugify(f"{name}_{osm['id']}")
    yes += 1
    results.append({
        "id": cid,
        "name": name,
        "address": address,
        "lat": lat,
        "lng": lng,
        "courts": osm.get("member_count", 1),
        "image": img or "",
        "classification": "yes",
        "summary": summary,
        "backboard_quote": None,
        "features_quote": features,
        "source_url": f"https://www.openstreetmap.org/{osm['id'].split('_')[0]}/{osm['id'].split('_')[1]}",
    })

result = {
    "query": QUERY,
    "target_feature": "basketball court",
    "location_type": "basketball court",
    "area": "Seattle",
    "stats": {"yes": yes, "no": no, "unclear": unclear},
    "total": len(results),
    "results": results,
}

cache_path = ROOT / f"cache/queries/{QUERY_HASH}.json"
cache_path.parent.mkdir(parents=True, exist_ok=True)
cache_path.write_text(json.dumps(result, indent=2))
print(f"\n[✓] wrote {cache_path}")
print(f"[✓] {len(results)} courts — yes={yes}")
print(f"[→] http://localhost:5001/results/{QUERY_HASH}")
