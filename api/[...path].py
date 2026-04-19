"""Vercel serverless entry point — minimal, fetches data from GitHub at runtime."""
from flask import Flask, render_template, jsonify, request
import os, sys, json
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))

# Query data fetched from GitHub raw at runtime (no local files needed)
_GITHUB_RAW = "https://raw.githubusercontent.com/neilpotdukhe/satellite-scout/main/api/data"
_QUERY_IDS = ["32a57c724db6", "5e9fb082e2a8", "7db00582db8b", "d92542d1992b", "ff49366238aa"]
_cache = {}


def _load_query(qid):
    if qid in _cache:
        return _cache[qid]
    try:
        import requests
        r = requests.get(f"{_GITHUB_RAW}/{qid}.json", timeout=8)
        if r.status_code == 200:
            _cache[qid] = r.json()
            return _cache[qid]
    except Exception:
        pass
    return None


def _load_all_queries():
    out = []
    for qid in _QUERY_IDS:
        d = _load_query(qid)
        if d:
            out.append({
                "query_id": qid,
                "query": d.get("query", ""),
                "target_feature": d.get("target_feature", ""),
                "location_type": d.get("location_type", ""),
                "area": d.get("area", ""),
                "total": d.get("total", 0),
                "stats": d.get("stats", {}),
            })
    return out


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
    return jsonify({"queries": _load_all_queries()})


@app.route("/api/query/<query_id>")
def api_query(query_id):
    d = _load_query(query_id)
    if not d:
        return jsonify({"error": "not found"}), 404
    return jsonify(d)


@app.route("/api/status/<query_id>")
def api_status(query_id):
    d = _load_query(query_id)
    if d:
        return jsonify({"status": "complete", "query_id": query_id})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/court/<query_id>/<court_id>")
def api_court(query_id, court_id):
    d = _load_query(query_id)
    if not d:
        return jsonify({"error": "query not found"}), 404
    for court in d.get("results", []):
        if court.get("id") == court_id:
            return jsonify(court)
    return jsonify({"error": "court not found"}), 404


@app.route("/api/scans")
def api_scans():
    return jsonify({"scans": []})


@app.route("/api/scan/create", methods=["POST"])
def api_scan_create():
    return jsonify({"error": "Scan creation needs the local server. This is read-only."}), 501


@app.route("/gov")
def gov_home():
    return render_template("gov_home.html")


@app.route("/api/gov/meetings")
def api_gov_meetings():
    return jsonify({"meetings": []})
