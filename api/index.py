"""Vercel serverless entry point — serves Scout UI + embedded cached results."""
from flask import Flask, render_template, jsonify, request
import os, sys, json
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

# Import embedded query data (no filesystem dependency)
from cached_data import QUERIES

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
    for qid, data in QUERIES.items():
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
    if query_id not in QUERIES:
        return jsonify({"error": "not found"}), 404
    return jsonify(QUERIES[query_id])


@app.route("/api/status/<query_id>")
def api_status(query_id):
    if query_id in QUERIES:
        return jsonify({"status": "complete", "query_id": query_id})
    return jsonify({"status": "not_found"}), 404


@app.route("/api/court/<query_id>/<court_id>")
def api_court(query_id, court_id):
    if query_id not in QUERIES:
        return jsonify({"error": "query not found"}), 404
    for court in QUERIES[query_id].get("results", []):
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
