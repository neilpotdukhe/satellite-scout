"""Vercel serverless entry point."""
from flask import Flask, render_template, jsonify
import os, sys
from pathlib import Path

# Add project root to path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)

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

@app.route("/api/queries")
def api_queries():
    return jsonify({"queries": []})

@app.route("/api/scans")
def api_scans():
    return jsonify({"scans": []})
