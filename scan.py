"""
Scan engine — polygon + natural-language query → visual search over satellite imagery.

Flow:
1. User draws a polygon + types a query ("tennis courts").
2. create_scan() tiles the polygon at a useful zoom, fetches Google satellite tiles,
   composites them into 800px scan images, and writes a manifest.
3. Claude Code (or any vision model) picks up the manifest, analyzes each pending tile
   (Read tool -> JSON with detections + pixel bboxes), and writes results back.
4. aggregate_scan() dedupes detections and returns a final result set.

No AI runs in this module — this is purely the imagery + manifest plumbing.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Any

import requests
from PIL import Image
from io import BytesIO


import os as _os
SCANS_DIR = Path("/tmp/cache/scans") if _os.environ.get("VERCEL") or _os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else Path("cache/scans")
TILE_SIZE = 256        # Google tile size in pixels
COMPOSITE_TILES = 3    # 3x3 composite -> 768px composite (we upscale slightly to 800)
COMPOSITE_PX = 800     # final scan image size
DEFAULT_ZOOM = 19      # ~0.2 m/px at Seattle latitude

# Wide-area scan settings (for covering miles)
WIDE_COMPOSITE_TILES = 5    # 5x5 tiles per composite = 1280px raw
WIDE_COMPOSITE_PX = 1200    # bigger output for more detail
WIDE_ZOOM = 18              # ~0.4 m/px — 4x area per tile vs zoom 19
MAX_WORKERS = 32            # parallel tile fetches (up from 8)


# ---- Web Mercator math ----

def lat_lng_to_tile(lat: float, lng: float, zoom: int) -> tuple[float, float]:
    """Return fractional Google Mercator tile coordinates."""
    n = 2 ** zoom
    x = (lng + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lat_lng(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Inverse: fractional tile coords -> lat/lng of top-left corner."""
    n = 2 ** zoom
    lng = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lng


def meters_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)


# ---- Manifest schema ----

@dataclass
class Detection:
    pixel_bbox: list[int]          # [x, y, w, h] in the composite image
    lat: float
    lng: float
    confidence: str                 # "high" | "medium" | "low"
    description: str = ""


@dataclass
class ScanTile:
    tile_id: str
    center_lat: float
    center_lng: float
    bbox: list[float]               # [west, south, east, north] in lat/lng
    image_path: str
    status: str = "pending"         # pending | done | error
    detections: list[dict] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ScanManifest:
    scan_id: str
    query: str
    polygon: list[list[float]]      # list of [lat, lng]
    bbox: list[float]               # [west, south, east, north]
    zoom: int
    composite_size: int
    created_at: float
    tiles: list[dict] = field(default_factory=list)
    status: str = "pending"
    note: Optional[str] = None


def _scan_dir(scan_id: str) -> Path:
    d = SCANS_DIR / scan_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "tiles").mkdir(exist_ok=True)
    return d


def _manifest_path(scan_id: str) -> Path:
    return _scan_dir(scan_id) / "manifest.json"


def load_manifest(scan_id: str) -> Optional[dict]:
    p = _manifest_path(scan_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_manifest(manifest: dict):
    p = _manifest_path(manifest["scan_id"])
    p.write_text(json.dumps(manifest, indent=2))


# ---- Polygon -> tile grid ----

def polygon_bbox(polygon: list[list[float]]) -> list[float]:
    """Return [west, south, east, north] for a polygon [[lat, lng], ...]."""
    lats = [p[0] for p in polygon]
    lngs = [p[1] for p in polygon]
    return [min(lngs), min(lats), max(lngs), max(lats)]


def _point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test. polygon is [[lat, lng], ...]."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygon[i][0], polygon[i][1]
        lat_j, lng_j = polygon[j][0], polygon[j][1]
        if ((lng_i > lng) != (lng_j > lng)) and \
                (lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i + 1e-12) + lat_i):
            inside = not inside
        j = i
    return inside


def tile_grid(polygon: list[list[float]], zoom: int = DEFAULT_ZOOM) -> list[tuple[int, int]]:
    """Return the list of Google tile (x, y) integer indices that cover the polygon.
    We include every tile whose center falls in the bbox of the polygon.
    For thin polygons we rely on the bbox — slightly over-covers but simpler.
    """
    west, south, east, north = polygon_bbox(polygon)
    x_tl, y_tl = lat_lng_to_tile(north, west, zoom)
    x_br, y_br = lat_lng_to_tile(south, east, zoom)
    x0, y0 = int(math.floor(x_tl)), int(math.floor(y_tl))
    x1, y1 = int(math.floor(x_br)), int(math.floor(y_br))
    tiles = []
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            tiles.append((tx, ty))
    return tiles


def composite_grid(polygon: list[list[float]], zoom: int = DEFAULT_ZOOM,
                    composite_tiles: int = COMPOSITE_TILES) -> list[dict]:
    """Produce a list of composite scan tiles covering the polygon.
    Each composite is composite_tiles x composite_tiles raw Google tiles.

    Returns: [{tile_id, center_lat, center_lng, tile_indices: [(tx, ty), ...], bbox: [w,s,e,n]}]
    """
    west, south, east, north = polygon_bbox(polygon)

    # Start from top-left tile aligned to composite boundaries
    x_tl, y_tl = lat_lng_to_tile(north, west, zoom)
    x_br, y_br = lat_lng_to_tile(south, east, zoom)

    # Align to composite grid — round the top-left down to a multiple of composite_tiles
    start_x = int(math.floor(x_tl / composite_tiles)) * composite_tiles
    start_y = int(math.floor(y_tl / composite_tiles)) * composite_tiles
    end_x = int(math.ceil(x_br / composite_tiles)) * composite_tiles
    end_y = int(math.ceil(y_br / composite_tiles)) * composite_tiles

    composites = []
    for cy in range(start_y, end_y, composite_tiles):
        for cx in range(start_x, end_x, composite_tiles):
            # Bounding box of this composite
            top_lat, west_lng = tile_to_lat_lng(cx, cy, zoom)
            bot_lat, east_lng = tile_to_lat_lng(cx + composite_tiles, cy + composite_tiles, zoom)
            center_lat = (top_lat + bot_lat) / 2
            center_lng = (west_lng + east_lng) / 2

            # Skip composites whose center falls outside the polygon (loose test — we include the
            # whole bbox for rectangular polygons, which is the common case)
            if not _point_in_polygon(center_lat, center_lng, polygon):
                # For rectangular / slightly irregular polygons, check if any corner is inside
                corners = [(top_lat, west_lng), (top_lat, east_lng), (bot_lat, west_lng), (bot_lat, east_lng)]
                if not any(_point_in_polygon(c[0], c[1], polygon) for c in corners):
                    continue

            tile_indices = [(cx + dx, cy + dy)
                            for dy in range(composite_tiles)
                            for dx in range(composite_tiles)]
            composites.append({
                "tile_id": f"c_{cx}_{cy}",
                "center_lat": center_lat,
                "center_lng": center_lng,
                "bbox": [west_lng, bot_lat, east_lng, top_lat],
                "tile_indices": tile_indices,
                "zoom": zoom,
            })
    return composites


# ---- Google tile fetcher ----

def _fetch_google_tile(x: int, y: int, z: int) -> Optional[Image.Image]:
    url = f"https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    if r.status_code != 200 or len(r.content) < 1000:
        return None
    return Image.open(BytesIO(r.content))


def fetch_composite(tile_indices: list[tuple[int, int]], zoom: int, out_path: Path,
                    composite_px: int = COMPOSITE_PX) -> bool:
    """Fetch the N×N raw tiles and stitch into a single square composite at composite_px.
    Composite_tiles is inferred from len(tile_indices) == composite_tiles^2.
    """
    n2 = len(tile_indices)
    composite_tiles = int(round(math.sqrt(n2)))
    raw_px = composite_tiles * TILE_SIZE

    imgs: dict[tuple[int, int], Image.Image] = {}
    min_x = min(t[0] for t in tile_indices)
    min_y = min(t[1] for t in tile_indices)

    from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

    def _get_tile(txy):
        tx, ty = txy
        return (tx - min_x, ty - min_y), _fetch_google_tile(tx, ty, zoom)

    with ThreadPoolExecutor(max_workers=min(n2, 25)) as pool:
        for (dx, dy), img in pool.map(lambda t: _get_tile(t), tile_indices):
            if img is not None:
                imgs[(dx, dy)] = img

    if len(imgs) < n2 * 0.8:
        return False

    composite = Image.new("RGB", (raw_px, raw_px))
    for (dx, dy), img in imgs.items():
        composite.paste(img, (dx * TILE_SIZE, dy * TILE_SIZE))

    # Resize to composite_px if needed
    if raw_px != composite_px:
        composite = composite.resize((composite_px, composite_px), Image.LANCZOS)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path, quality=90)
    return True


# ---- Create a new scan ----

def _estimate_area_sq_miles(polygon: list[list[float]]) -> float:
    """Rough area estimate for the polygon bbox in square miles."""
    west, south, east, north = polygon_bbox(polygon)
    lat_mid = (north + south) / 2
    width_m = abs(east - west) * 111320 * math.cos(math.radians(lat_mid))
    height_m = abs(north - south) * 111320
    return (width_m * height_m) / (1609.34 ** 2)


def create_scan(query: str, polygon: list[list[float]], zoom: int = None,
                wide_mode: bool = False) -> dict:
    """Create a new scan: generate the grid, fetch tiles, save manifest. Returns manifest.

    If wide_mode=True or area > 0.5 sq mi, automatically uses lower zoom + bigger composites
    for faster coverage of large areas.
    """
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    scan_id = hashlib.md5(f"{query}{polygon}{time.time()}".encode()).hexdigest()[:12]

    # Auto-detect wide mode based on polygon area
    area_sqmi = _estimate_area_sq_miles(polygon)
    if wide_mode or area_sqmi > 0.5:
        effective_zoom = zoom or WIDE_ZOOM
        effective_composite_tiles = WIDE_COMPOSITE_TILES
        effective_composite_px = WIDE_COMPOSITE_PX
        workers = MAX_WORKERS
    else:
        effective_zoom = zoom or DEFAULT_ZOOM
        effective_composite_tiles = COMPOSITE_TILES
        effective_composite_px = COMPOSITE_PX
        workers = MAX_WORKERS  # always use max workers now

    composites = composite_grid(polygon, zoom=effective_zoom,
                                composite_tiles=effective_composite_tiles)

    scan_dir = _scan_dir(scan_id)
    manifest = {
        "scan_id": scan_id,
        "query": query,
        "polygon": polygon,
        "bbox": polygon_bbox(polygon),
        "zoom": effective_zoom,
        "composite_size": effective_composite_px,
        "composite_tiles": effective_composite_tiles,
        "area_sq_miles": round(area_sqmi, 2),
        "created_at": time.time(),
        "tiles": [],
        "status": "fetching",
        "total_composites": len(composites),
    }
    save_manifest(manifest)

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(comp):
        img_path = scan_dir / "tiles" / f"{comp['tile_id']}.jpg"
        ok = fetch_composite(comp["tile_indices"], comp["zoom"], img_path,
                            composite_px=effective_composite_px)
        tile = {
            "tile_id": comp["tile_id"],
            "center_lat": comp["center_lat"],
            "center_lng": comp["center_lng"],
            "bbox": comp["bbox"],
            "image_path": f"/cache/scans/{scan_id}/tiles/{comp['tile_id']}.jpg",
            "image_fs_path": str(img_path),
            "status": "pending" if ok else "fetch_error",
            "detections": [],
        }
        if not ok:
            tile["error"] = "failed to fetch tiles"
        return tile

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch_one, c) for c in composites]
        for fut in as_completed(futures):
            manifest["tiles"].append(fut.result())
            # Periodic save so the UI can show progress
            if len(manifest["tiles"]) % 3 == 0:
                save_manifest(manifest)

    manifest["tiles"].sort(key=lambda t: (t["center_lat"], t["center_lng"]))
    manifest["status"] = "ready_for_analysis"
    elapsed = time.time() - manifest["created_at"]
    manifest["fetch_time_sec"] = round(elapsed, 1)
    manifest["note"] = (f"Fetched {len(manifest['tiles'])} composites in {elapsed:.0f}s "
                        f"({area_sqmi:.1f} sq mi at zoom {effective_zoom})")
    save_manifest(manifest)
    return manifest


# ---- Pixel <-> lat/lng conversion for detections ----

def pixel_to_lat_lng(pixel_x: float, pixel_y: float,
                     tile_bbox: list[float], composite_px: int) -> tuple[float, float]:
    """Convert a pixel coordinate inside a composite tile to lat/lng.
    NOTE: approximate — treats the tile as linear in lat/lng, which is fine for small tiles.
    tile_bbox = [west, south, east, north]
    """
    west, south, east, north = tile_bbox
    frac_x = pixel_x / composite_px
    frac_y = pixel_y / composite_px
    lng = west + frac_x * (east - west)
    lat = north - frac_y * (north - south)   # y grows downward in images
    return lat, lng


def pixel_bbox_to_lat_lng(bbox: list[int], tile_bbox: list[float],
                          composite_px: int) -> tuple[float, float]:
    """Return the (lat, lng) of the CENTER of a pixel bbox [x, y, w, h]."""
    x, y, w, h = bbox
    cx = x + w / 2
    cy = y + h / 2
    return pixel_to_lat_lng(cx, cy, tile_bbox, composite_px)


# ---- Aggregation / dedup ----

def aggregate_scan(scan_id: str, dedup_radius_m: float = 40.0) -> dict:
    """Collect all detections from a scan, convert to lat/lng, dedupe.
    Returns updated manifest with an 'aggregated' key.
    """
    manifest = load_manifest(scan_id)
    if not manifest:
        raise FileNotFoundError(scan_id)

    all_dets = []
    for tile in manifest["tiles"]:
        for det in tile.get("detections", []):
            if "pixel_bbox" not in det:
                continue
            lat, lng = pixel_bbox_to_lat_lng(
                det["pixel_bbox"], tile["bbox"], manifest["composite_size"]
            )
            all_dets.append({
                "lat": lat,
                "lng": lng,
                "confidence": det.get("confidence", "medium"),
                "description": det.get("description", ""),
                "tile_id": tile["tile_id"],
                "pixel_bbox": det["pixel_bbox"],
                "tile_image": tile["image_path"],
            })

    # Dedupe within dedup_radius_m
    def haversine(lat1, lng1, lat2, lng2):
        R = 6371000
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(a))

    deduped: list[dict] = []
    used = [False] * len(all_dets)
    for i, det in enumerate(all_dets):
        if used[i]:
            continue
        group = [det]
        used[i] = True
        for j in range(i + 1, len(all_dets)):
            if used[j]:
                continue
            if haversine(det["lat"], det["lng"], all_dets[j]["lat"], all_dets[j]["lng"]) < dedup_radius_m:
                group.append(all_dets[j])
                used[j] = True
        # Keep the highest-confidence member of the group
        rank = {"high": 3, "medium": 2, "low": 1}
        group.sort(key=lambda d: -rank.get(d["confidence"], 0))
        deduped.append(group[0])

    manifest["aggregated"] = {
        "total_raw_detections": len(all_dets),
        "total_unique_detections": len(deduped),
        "detections": deduped,
    }

    analyzed = sum(1 for t in manifest["tiles"] if t["status"] in ("done", "fetch_error"))
    if analyzed == len(manifest["tiles"]):
        manifest["status"] = "complete"

    save_manifest(manifest)
    return manifest
