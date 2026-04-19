"""
Satellite Scout — Flask Web App

Spatial queries with satellite imagery + authoritative web sources.

Run: python app.py
Then open: http://localhost:5001
"""

from flask import Flask, render_template, request, jsonify, send_from_directory
import json
import hashlib
import subprocess
import os
from pathlib import Path
from typing import Optional

from pipeline import run_query, get_seattle_tennis_results
from cache import query_cache, ensure_cache_dirs
import scan as scan_engine
from gov import scraper as gov_scraper
from gov import fetcher as gov_fetcher
from gov import extractor as gov_extractor
from gov import validator as gov_validator


def spawn_scout_subprocess(scan_id: str) -> Optional[int]:
    """Fire-and-forget spawn of a child Claude Code process that runs /scout scan <id>.

    Returns the child PID if launched, or None if cc is not available.
    The child runs in the background; Flask polling picks up manifest updates.
    """
    # Log output to per-scan file for debugging
    scan_dir = Path(f"cache/scans/{scan_id}")
    scan_dir.mkdir(parents=True, exist_ok=True)
    log_path = scan_dir / "scout.log"

    env = os.environ.copy()
    # Disable Claude Code's own auto-memory + keep it bare for faster boot
    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        f"/scout scan {scan_id}",
    ]

    try:
        log_file = open(log_path, "wb")
        proc = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(Path.cwd()),
            env=env,
            start_new_session=True,  # detach so it keeps running even if Flask restarts
        )
        return proc.pid
    except FileNotFoundError:
        return None  # cc not installed
    except Exception as e:
        print(f"[spawn_scout] error: {e}")
        return None

app = Flask(__name__)
ensure_cache_dirs()


@app.route("/")
def index():
    """Home page with query input."""
    return render_template("index.html")


@app.route("/api/query", methods=["POST"])
def api_query():
    """Process a natural language query.

    Cache hit → return results immediately.
    Cache miss → return a "pending" response with instructions to run the Scout skill
                 in Claude Code. No automatic fallback to the generic pipeline, because
                 Claude Code can do a much better job researching the query.
    """
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "empty query"}), 400

    query_hash = hashlib.md5(query.lower().encode()).hexdigest()[:12]

    # Check cache first
    cached = query_cache.get(query_hash)
    if cached:
        return jsonify({"query": query, "query_id": query_hash, "cached": True, **cached})

    # Cache miss — tell the client to run the Scout skill
    return jsonify({
        "query": query,
        "query_id": query_hash,
        "cached": False,
        "pending": True,
        "command": f"/scout {query}",
        "message": "This query hasn't been scouted yet. Run the command in Claude Code — "
                   "the Scout skill will research the query, cache the results, and this "
                   "page will auto-refresh to show them.",
    })


@app.route("/api/status/<query_id>")
def api_status(query_id):
    """Poll endpoint: checks whether a given query_id has been cached yet."""
    cached = query_cache.get(query_id)
    if cached:
        return jsonify({"ready": True, "query_id": query_id})
    return jsonify({"ready": False, "query_id": query_id})


@app.route("/results/<query_id>")
def results_page(query_id):
    """Results page."""
    cached = query_cache.get(query_id)
    if not cached:
        return "Query not found", 404
    return render_template("results.html", query_id=query_id, **cached)


@app.route("/api/court/<query_id>/<path:court_id>")
def api_court(query_id, court_id):
    """Get full source record for a specific court within a specific query's cache."""
    cached = query_cache.get(query_id)
    if not cached:
        return jsonify({"error": "query not cached"}), 404
    court = next((c for c in cached.get("results", []) if str(c.get("id")) == court_id), None)
    if not court:
        return jsonify({"error": "court not found in query"}), 404
    return jsonify(court)


@app.route("/api/queries")
def api_queries():
    """List all cached queries with preview stats."""
    queries = []
    for f in sorted(Path("cache/queries").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text())
            queries.append({
                "query_id": f.stem,
                "query": data.get("query", ""),
                "target_feature": data.get("target_feature", ""),
                "location_type": data.get("location_type", ""),
                "area": data.get("area", ""),
                "stats": data.get("stats", {}),
                "total": data.get("total", 0),
            })
        except Exception:
            continue
    return jsonify({"queries": queries})


@app.route("/static/images/courts/<path:filename>")
def serve_court_image(filename):
    return send_from_directory("static/images/courts", filename)


@app.route("/static/cache-images/<path:filename>")
def serve_cache_image(filename):
    return send_from_directory("cache/images", filename)


# ---------- Scan: polygon visual search ----------

@app.route("/scan")
def scan_page():
    """Polygon-draw map UI for visual search over satellite imagery."""
    return render_template("scan.html")


@app.route("/api/scan/create", methods=["POST"])
def api_scan_create():
    """Create a new scan job: tile the polygon, fetch Google tiles, save manifest.

    Body: { "query": str, "polygon": [[lat, lng], ...], "zoom": int (optional) }
    Returns: { "scan_id": str, "tile_count": int, "command": "/scout scan <id>" }
    """
    data = request.get_json() or {}
    query = (data.get("query") or "").strip()
    polygon = data.get("polygon") or []
    zoom = int(data.get("zoom") or scan_engine.DEFAULT_ZOOM)

    if not query:
        return jsonify({"error": "query is required"}), 400
    if not polygon or len(polygon) < 3:
        return jsonify({"error": "polygon must have at least 3 points"}), 400

    # Size check with auto-suggest fallback zoom
    comps = scan_engine.composite_grid(polygon, zoom=zoom)
    HARD_LIMIT = 500

    if len(comps) > HARD_LIMIT:
        # Try one zoom level down to see if it fits
        fallback_zoom = max(zoom - 1, 16)
        fallback_comps = scan_engine.composite_grid(polygon, zoom=fallback_zoom)
        return jsonify({
            "error": f"polygon too large: {len(comps)} tiles at zoom {zoom}. "
                     f"Try zoom {fallback_zoom} ({len(fallback_comps)} tiles) or draw a smaller area.",
            "suggested_zoom": fallback_zoom if len(fallback_comps) <= HARD_LIMIT else None,
            "current_tiles": len(comps),
            "hard_limit": HARD_LIMIT,
        }), 400

    # Run fetch synchronously for MVP (fast for <100 tiles)
    manifest = scan_engine.create_scan(query, polygon, zoom=zoom)
    scan_id = manifest["scan_id"]

    # Auto-spawn a Claude Code child process to analyze the tiles.
    # It runs in the background and writes detections to the manifest;
    # the frontend polling picks up updates.
    auto_run = (data.get("auto_run") is not False)  # default: on
    child_pid = None
    if auto_run:
        child_pid = spawn_scout_subprocess(scan_id)

    return jsonify({
        "scan_id": scan_id,
        "tile_count": len(manifest["tiles"]),
        "status": manifest["status"],
        "command": f"/scout scan {scan_id}",
        "view_url": f"/scan/{scan_id}",
        "auto_run": auto_run and child_pid is not None,
        "child_pid": child_pid,
    })


@app.route("/api/scan/<scan_id>")
def api_scan_get(scan_id):
    """Return the current manifest + aggregated detections."""
    manifest = scan_engine.load_manifest(scan_id)
    if not manifest:
        return jsonify({"error": "scan not found"}), 404

    # Recompute aggregation live so new detections show up as they're written
    try:
        manifest = scan_engine.aggregate_scan(scan_id)
    except Exception as e:
        manifest["aggregate_error"] = str(e)
    return jsonify(manifest)


@app.route("/api/scan/<scan_id>/status")
def api_scan_status(scan_id):
    """Lightweight polling endpoint with progress numbers only."""
    manifest = scan_engine.load_manifest(scan_id)
    if not manifest:
        return jsonify({"error": "scan not found"}), 404
    total = len(manifest["tiles"])
    done = sum(1 for t in manifest["tiles"] if t["status"] == "done")
    errors = sum(1 for t in manifest["tiles"] if t["status"] not in ("pending", "done"))
    total_dets = sum(len(t.get("detections", [])) for t in manifest["tiles"])
    return jsonify({
        "scan_id": scan_id,
        "status": manifest["status"],
        "total_tiles": total,
        "tiles_done": done,
        "tiles_error": errors,
        "tiles_pending": total - done - errors,
        "raw_detections": total_dets,
    })


@app.route("/scan/<scan_id>")
def scan_result_page(scan_id):
    """Render the scan results page (same template, just auto-loads the scan)."""
    return render_template("scan.html", scan_id=scan_id)


@app.route("/api/scans")
def api_scans_list():
    """List all past scans with preview metadata."""
    scans = []
    scans_dir = Path("cache/scans")
    if scans_dir.exists():
        for d in scans_dir.iterdir():
            if not d.is_dir():
                continue
            mf = d / "manifest.json"
            if not mf.exists():
                continue
            try:
                m = json.loads(mf.read_text())
                tiles = m.get("tiles", [])
                done = sum(1 for t in tiles if t.get("status") == "done")
                unique = m.get("aggregated", {}).get("total_unique_detections", 0)
                raw = sum(len(t.get("detections", [])) for t in tiles)

                # Grab a representative tile image for the preview (first "done" tile, or first tile)
                preview_img = None
                for t in tiles:
                    if t.get("image_path"):
                        preview_img = t["image_path"]
                        break

                scans.append({
                    "scan_id": m["scan_id"],
                    "query": m.get("query", ""),
                    "zoom": m.get("zoom"),
                    "bbox": m.get("bbox"),
                    "polygon": m.get("polygon"),
                    "status": m.get("status"),
                    "tile_count": len(tiles),
                    "tiles_done": done,
                    "raw_detections": raw,
                    "unique_detections": unique,
                    "created_at": m.get("created_at"),
                    "preview_image": preview_img,
                })
            except Exception:
                continue
    scans.sort(key=lambda s: s.get("created_at") or 0, reverse=True)
    return jsonify({"scans": scans})


@app.route("/cache/scans/<path:path>")
def serve_scan_file(path):
    return send_from_directory("cache/scans", path)


# ---------- Gov: Seattle City Council meeting intelligence ----------

@app.route("/gov")
def gov_home():
    """Home page: list of meetings with filter/search."""
    return render_template("gov_home.html")


@app.route("/api/gov/meetings")
def api_gov_meetings():
    """List meetings from the scraper (with extraction status from cache)."""
    limit = int(request.args.get("limit", 30))
    refresh = request.args.get("refresh") == "1"

    meetings_cache = Path("cache/gov/scraped_meetings.json")
    if refresh or not meetings_cache.exists():
        try:
            meetings = gov_scraper.scrape_meetings(limit=limit)
            meetings_cache.parent.mkdir(parents=True, exist_ok=True)
            meetings_cache.write_text(json.dumps([m.to_dict() for m in meetings], indent=2))
        except Exception as e:
            return jsonify({"error": f"scrape failed: {e}"}), 500
    else:
        try:
            meetings = [gov_scraper.Meeting(**m) for m in json.loads(meetings_cache.read_text())[:limit]]
        except Exception:
            meetings = gov_scraper.scrape_meetings(limit=limit)

    # Enrich with extraction status
    out = []
    for m in meetings:
        d = m.to_dict()
        d["has_transcript"] = (gov_fetcher.meeting_dir(m.video_id) / "transcript.json").exists()
        d["has_extraction"] = gov_extractor.is_extracted(m.video_id)
        # If extracted, pull summary fields for preview
        if d["has_extraction"]:
            ex = gov_extractor.load_extracted(m.video_id)
            d["headline"] = ex.get("headline", "")
            d["summary_preview"] = (ex.get("summary") or "")[:200]
            d["topic_count"] = len(ex.get("topics", []))
            d["high_importance_count"] = sum(
                1 for t in ex.get("topics", []) if t.get("importance") == "high"
            )
            d["bills_count"] = len(ex.get("bills_mentioned", []))
            try:
                vr = gov_validator.validate_extraction(m.video_id)
                d["validation_passed"] = vr.passed
                d["validation_error_count"] = sum(1 for i in vr.issues if i["level"] == "error")
                d["validation_warning_count"] = sum(1 for i in vr.issues if i["level"] == "warning")
            except Exception:
                d["validation_passed"] = None
        out.append(d)
    return jsonify({"meetings": out})


@app.route("/api/gov/meeting/<meeting_id>/fetch", methods=["POST"])
def api_gov_fetch(meeting_id):
    """Download the SRT + parse into transcript.json for a meeting."""
    m_dict = None
    cache_file = Path("cache/gov/scraped_meetings.json")
    if cache_file.exists():
        for d in json.loads(cache_file.read_text()):
            if d["video_id"] == meeting_id:
                m_dict = d
                break
    if not m_dict:
        meetings = gov_scraper.scrape_meetings(limit=60)
        for mm in meetings:
            if mm.video_id == meeting_id:
                m_dict = mm.to_dict()
                break
    if not m_dict:
        return jsonify({"error": "meeting not found"}), 404

    meeting = gov_scraper.Meeting(**m_dict)
    gov_fetcher.save_metadata(meeting)
    try:
        srt_path = gov_fetcher.fetch_srt(meeting)
        tpath = gov_fetcher.write_transcript_json(meeting)
    except Exception as e:
        return jsonify({"error": f"fetch failed: {e}"}), 500

    return jsonify({
        "meeting_id": meeting_id,
        "srt": str(srt_path),
        "transcript_json": str(tpath),
        "status": "ready_for_extraction",
    })


@app.route("/api/gov/meeting/<meeting_id>/extract", methods=["POST"])
def api_gov_extract(meeting_id):
    """Spawn a Claude Code child process to extract decisions from the transcript."""
    # Ensure transcript exists first
    mdir = gov_fetcher.meeting_dir(meeting_id)
    if not (mdir / "transcript.json").exists():
        return jsonify({"error": "transcript not yet fetched"}), 400

    pid = gov_extractor.spawn_extractor(meeting_id)
    if pid is None:
        return jsonify({"error": "failed to spawn cc subprocess"}), 500
    return jsonify({
        "meeting_id": meeting_id,
        "child_pid": pid,
        "command": f"/gov extract {meeting_id}",
    })


@app.route("/api/gov/meeting/<meeting_id>")
def api_gov_meeting(meeting_id):
    """Return the full meeting record (metadata + transcript excerpt + extraction + validation)."""
    mdir = gov_fetcher.meeting_dir(meeting_id)
    metadata = gov_fetcher.load_metadata(meeting_id)
    if not metadata:
        return jsonify({"error": "meeting not found"}), 404

    transcript = None
    tpath = mdir / "transcript.json"
    if tpath.exists():
        d = json.loads(tpath.read_text())
        transcript = {
            "total_paragraphs": d["total_paragraphs"],
            "duration_sec": d["duration_sec"],
            "paragraphs": d["paragraphs"],   # full list; UI can paginate if needed
        }

    extracted = gov_extractor.load_extracted(meeting_id)

    validation = None
    if extracted:
        try:
            vr = gov_validator.validate_extraction(meeting_id)
            validation = {
                "passed": vr.passed,
                "issues": vr.issues,
                "stats": vr.stats,
            }
        except Exception as e:
            validation = {"passed": None, "error": str(e)}

    return jsonify({
        "metadata": metadata,
        "transcript": transcript,
        "extracted": extracted,
        "validation": validation,
    })


@app.route("/api/gov/validation")
def api_gov_validation():
    """Run the validator over every extracted meeting and return results."""
    results = gov_validator.validate_all()
    return jsonify({
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "results": [
            {
                "meeting_id": r.meeting_id,
                "passed": r.passed,
                "issues": r.issues,
                "stats": r.stats,
            } for r in results
        ],
    })


@app.route("/gov/meetings/<meeting_id>")
def gov_meeting_page(meeting_id):
    return render_template("gov_meeting.html", meeting_id=meeting_id)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
