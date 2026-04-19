"""Core pipeline: query → parse → find locations → gather sources → classify → return."""

import json
from pathlib import Path
from parser import parse_query


def get_seattle_tennis_results() -> dict:
    """Return preprocessed Seattle tennis court results (the canonical demo data)."""
    with open("data/courts.json") as f:
        courts = json.load(f)
    return _build_result_payload(
        query="tennis courts in Seattle with hitting walls",
        courts=courts,
        target_feature="hitting wall",
    )


def _build_result_payload(query: str, courts: list, target_feature: str) -> dict:
    """Shape the result object returned to the frontend."""
    # Classify each court
    classified = []
    for c in courts:
        status = c.get("backboard_present", "not_mentioned")
        # Map to UI categories
        if status == "yes":
            ui_class = "yes"
        elif status == "no":
            ui_class = "no"
        else:
            ui_class = "unclear"

        classified.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "address": c.get("address", ""),
            "lat": c.get("lat"),
            "lng": c.get("lng"),
            "courts": c.get("courts"),
            "image": c.get("image"),
            "classification": ui_class,
            "summary": c.get("summary", ""),
            "backboard_quote": c.get("backboard_quote"),
            "features_quote": c.get("features_quote"),
            "source_url": c.get("source_url"),
        })

    # Count by classification
    stats = {
        "yes": sum(1 for c in classified if c["classification"] == "yes"),
        "no": sum(1 for c in classified if c["classification"] == "no"),
        "unclear": sum(1 for c in classified if c["classification"] == "unclear"),
    }

    return {
        "query": query,
        "target_feature": target_feature,
        "location_type": "tennis court",
        "area": "Seattle",
        "stats": stats,
        "total": len(classified),
        "results": classified,
    }


def run_query(query: str) -> dict:
    """Main pipeline entry point. Takes a natural language query and returns results."""
    # Step 1: parse the query
    parsed = parse_query(query)

    # Step 2: If this is the Seattle tennis canonical query, serve preprocessed data
    if (parsed.get("location_type", "").lower() in {"tennis court", "tennis courts"}
            and parsed.get("area", "").lower() == "seattle"
            and "wall" in parsed.get("target_feature", "").lower()):
        return get_seattle_tennis_results()

    # Step 3: Otherwise, run the generic pipeline (Step 3 of the build plan)
    from pipeline_generic import run_generic_query
    return run_generic_query(query, parsed)
