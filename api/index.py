"""Vercel serverless entry point — serves the Scout UI + bundled cached results."""
from flask import Flask, render_template, jsonify, request
import os, sys, json
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

# Bundled query results (cached locally, shipped with deploy)
DATA_DIR = Path(__file__).resolve().parent / "data"

app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))


def _load_cached_queries():
    """Load all bundled query JSON files."""
    queries = []
    if not DATA_DIR.exists():
        return queries
    for f in sorted(DATA_DIR.glob("*.json"), key=lambda p: p.name):
        try:
            data = json.loads(f.read_text())
            queries.append({
                "query_id": f.stem,
                "query": data.get("query", ""),
                "target_feature": data.get("target_feature", ""),
                "location_type": data.get("location_type", ""),
                "area": data.get("area", ""),
                "total": data.get("total", 0),
                "stats": data.get("stats", {}),
            })
        except Exception:
            continue
    return queries


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scan")
@app.route("/scan/<scan_id>")
def scan_page(scan_id=None):
    return render_template("scan.html", scan_id=scan_id)


@app.route("/results/<query_id>")
def results_page(query_id):
    return render_template("results.html")


@app.route("/api/queries")
def api_queries():
    return jsonify({"queries": _load_cached_queries()})


@app.route("/api/query/<query_id>")
def api_query(query_id):
    f = DATA_DIR / f"{query_id}.json"
    if not f.exists():
        return jsonify({"error": "not found"}), 404
    return jsonify(json.loads(f.read_text()))


@app.route("/api/status/<query_id>")
def api_status(query_id):
    f = DATA_DIR / f"{query_id}.json"
    if f.exists():
        return jsonify({"status": "complete", "query_id": query_id})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/court/<query_id>/<court_id>")
def api_court(query_id, court_id):
    f = DATA_DIR / f"{query_id}.json"
    if not f.exists():
        return jsonify({"error": "query not found"}), 404
    data = json.loads(f.read_text())
    for court in data.get("results", []):
        if court.get("id") == court_id:
            return jsonify(court)
    return jsonify({"error": "court not found"}), 404


@app.route("/api/scans")
def api_scans():
    return jsonify({"scans": []})


@app.route("/api/scan/create", methods=["POST"])
def api_scan_create():
    return jsonify({"error": "Scan creation requires the local server (python app.py). This is the read-only public deployment."}), 501


@app.route("/gov")
def gov_home():
    return render_template("gov_home.html")


@app.route("/api/gov/meetings")
def api_gov_meetings():
    return jsonify({"meetings": []})


@app.route("/_debug")
def debug():
    """Debug route to see what Flask receives."""
    return jsonify({
        "path": request.path,
        "url": request.url,
        "method": request.method,
        "headers": dict(request.headers),
    })
