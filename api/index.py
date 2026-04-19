"""Vercel serverless entry point."""
from flask import Flask, render_template, request, jsonify, send_from_directory
import os, sys, json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Minimal app for Vercel — import the full app
try:
    from app import app
except Exception as e:
    # Fallback minimal app if full import fails
    app = Flask(__name__,
                template_folder=str(Path(__file__).resolve().parent.parent / "templates"),
                static_folder=str(Path(__file__).resolve().parent.parent / "static"))

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/scan")
    def scan_page():
        return render_template("scan.html")

    @app.route("/api/queries")
    def api_queries():
        return jsonify({"queries": []})

    @app.route("/api/scans")
    def api_scans():
        return jsonify({"scans": []})

    @app.route("/_debug")
    def debug():
        return jsonify({"error": str(e), "fallback": True})
