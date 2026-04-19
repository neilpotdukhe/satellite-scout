"""Vercel serverless entry point — re-exports the Flask app."""
import sys
from pathlib import Path

# Add parent dir so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app
