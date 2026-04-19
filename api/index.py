"""Vercel serverless entry point — renders templates only, data is client-side."""
from flask import Flask, render_template, jsonify
import os, sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))

# All data endpoints return pointers to GitHub raw — client-side JS fetches directly
GITHUB_DATA = "https://raw.githubusercontent.com/neilpotdukhe/satellite-scout/main/api/data"
QUERY_IDS = ["32a57c724db6", "5e9fb082e2a8", "7db00582db8b", "d92542d1992b", "ff49366238aa"]


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/scan")
@app.route("/scan/<scan_id>")
def scan_page(scan_id=None):
    return render_template("scan.html", scan_id=scan_id)

@app.route("/results/<query_id>")
def results_page(query_id):
    return render_template("results_live.html", query_id=query_id)

@app.route("/gov")
def gov_home():
    return render_template("gov_home.html")

# Lightweight API that redirects to GitHub raw data
@app.route("/api/queries")
def api_queries():
    return jsonify({"queries": [], "data_source": GITHUB_DATA, "query_ids": QUERY_IDS})

@app.route("/api/scans")
def api_scans():
    return jsonify({"scans": []})
