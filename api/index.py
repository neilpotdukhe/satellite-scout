"""Vercel serverless entry point — serves Scout UI + embedded cached results."""
from flask import Flask, render_template, jsonify, request
import os, sys, json
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
API_DIR = str(Path(__file__).resolve().parent)
sys.path.insert(0, ROOT)
sys.path.insert(0, API_DIR)

# Load query data — try local files first, then fetch from GitHub
import requests as _req

_GITHUB_RAW = "https://raw.githubusercontent.com/neilpotdukhe/satellite-scout/main/api/data"
_QUERY_IDS = ["32a57c724db6", "5e9fb082e2a8", "7db00582db8b", "d92542d1992b", "ff49366238aa"]
_queries_cache = {}

def _get_queries():
    global _queries_cache
    if _queries_cache:
        return _queries_cache
    # Try local files first
    _data_dir = Path(__file__).resolve().parent / "data"
    for qid in _QUERY_IDS:
        local = _data_dir / f"{qid}.json"
        if local.exists():
            try:
                _queries_cache[qid] = json.loads(local.read_text())
                continue
            except Exception:
                pass
        # Fallback: fetch from GitHub
        try:
            r = _req.get(f"{_GITHUB_RAW}/{qid}.json", timeout=5)
            if r.status_code == 200:
                _queries_cache[qid] = r.json()
        except Exception:
            pass
    return _queries_cache

app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))


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
    queries = []
    for qid, data in _get_queries().items():
        queries.append({
            "query_id": qid,
            "query": data.get("query", ""),
            "target_feature": data.get("target_feature", ""),
            "location_type": data.get("location_type", ""),
            "area": data.get("area", ""),
            "total": data.get("total", 0),
            "stats": data.get("stats", {}),
        })
    return jsonify({"queries": queries})


@app.route("/api/query/<query_id>")
def api_query(query_id):
    if query_id not in _get_queries():
        return jsonify({"error": "not found"}), 404
    return jsonify(_get_queries()[query_id])


@app.route("/api/status/<query_id>")
def api_status(query_id):
    if query_id in _get_queries():
        return jsonify({"status": "complete", "query_id": query_id})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/court/<query_id>/<court_id>")
def api_court(query_id, court_id):
    if query_id not in _get_queries():
        return jsonify({"error": "query not found"}), 404
    for court in _get_queries()[query_id].get("results", []):
        if court.get("id") == court_id:
            return jsonify(court)
    return jsonify({"error": "court not found"}), 404


@app.route("/api/scans")
def api_scans():
    return jsonify({"scans": []})


@app.route("/api/scan/create", methods=["POST"])
def api_scan_create():
    return jsonify({"error": "Scan creation needs the local server (python app.py). This public deployment is read-only."}), 501


@app.route("/gov")
def gov_home():
    return render_template("gov_home.html")


@app.route("/api/gov/meetings")
def api_gov_meetings():
    return jsonify({"meetings": []})
