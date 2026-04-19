"""Query parsing — uses Claude API if available, falls back to heuristics."""

import os
import json
import re


# Hardcoded fallback mappings
LOCATION_TYPE_TAGS = {
    "tennis court": ['["leisure"="pitch"]["sport"="tennis"]'],
    "basketball court": ['["leisure"="pitch"]["sport"="basketball"]'],
    "soccer field": ['["leisure"="pitch"]["sport"="soccer"]'],
    "baseball field": ['["leisure"="pitch"]["sport"="baseball"]'],
    "parking lot": ['["amenity"="parking"]'],
    "park": ['["leisure"="park"]'],
    "swimming pool": ['["leisure"="swimming_pool"]'],
    "playground": ['["leisure"="playground"]'],
    "school": ['["amenity"="school"]'],
    "gas station": ['["amenity"="fuel"]'],
    "hospital": ['["amenity"="hospital"]'],
    "library": ['["amenity"="library"]'],
    "golf course": ['["leisure"="golf_course"]'],
}


def _heuristic_parse(query: str) -> dict:
    """Cheap regex-based fallback when no LLM is available.

    Strategy: split on "in"/"with" keywords first to isolate the type from the feature,
    then match each piece against the known location types.
    """
    q = query.lower().strip()

    # Split off the feature clause first so it can't pollute type matching
    target_feature = None
    feature_re = re.search(r"\b(?:with|that have|having)\s+(?:a |an )?(.+?)$", q)
    if feature_re:
        target_feature = feature_re.group(1).strip()
        # Trim the feature clause off the rest of the query
        q_stem = q[:feature_re.start()].strip()
    else:
        q_stem = q

    # Now find area (after "in") within the stem
    area = None
    area_re = re.search(r"\bin\s+([a-z][a-z\s]+?)$", q_stem)
    if area_re:
        area = area_re.group(1).strip().title()
        type_part = q_stem[:area_re.start()].strip()
    else:
        type_part = q_stem

    # Find the best location type match in type_part (prefer longer matches)
    location_type = None
    best_len = 0
    for lt in LOCATION_TYPE_TAGS:
        for candidate in (lt, lt + "s"):
            if candidate in type_part and len(candidate) > best_len:
                location_type = lt
                best_len = len(candidate)

    return {
        "location_type": location_type or "tennis court",
        "area": area or "Seattle",
        "target_feature": target_feature or "hitting wall",
        "osm_tags": LOCATION_TYPE_TAGS.get(location_type, LOCATION_TYPE_TAGS["tennis court"]),
        "visual_cue": f"visible {target_feature or 'feature'} from above",
        "parser": "heuristic",
    }


def _claude_parse(query: str) -> dict:
    """Parse query with Claude API. Returns None if no API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    system = (
        "You parse natural-language spatial queries into a structured format. "
        "Extract: location_type (e.g. 'tennis court'), area (city/region name), "
        "target_feature (what to find at each location, e.g. 'hitting wall'), "
        "osm_tags (OpenStreetMap tag selectors like '[\"leisure\"=\"pitch\"][\"sport\"=\"tennis\"]'), "
        "and visual_cue (how the feature would appear from above in a satellite image). "
        "Reply ONLY with a JSON object, no markdown, no prose."
    )
    user = f"Parse this spatial query: \"{query}\""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = response.content[0].text.strip()
        # Strip fences if any
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        parsed["parser"] = "claude"
        # Normalize osm_tags to a list
        tags = parsed.get("osm_tags")
        if isinstance(tags, str):
            parsed["osm_tags"] = [tags]
        return parsed
    except Exception as e:
        print(f"[parser] Claude parse failed: {e}")
        return None


def parse_query(query: str) -> dict:
    """Parse a natural language spatial query into structured format."""
    result = _claude_parse(query)
    if result:
        return result
    return _heuristic_parse(query)
