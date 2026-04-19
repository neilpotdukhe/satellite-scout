"""Scrape the Seattle Channel videos index for recent council meetings.

The page has a JS payload with a `playcapvideo(videoid, mp4url, thumb, desc, title, date, duration, ...)`
call per meeting. We pull those and produce structured records.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import requests


VIDEOS_INDEX_URL = "https://www.seattlechannel.org/mayor-and-council/city-council/city-council-all-videos-index"
VIDEO_SERVER = "https://video.seattle.gov"
CAPTION_BASE = "https://www.seattlechannel.org"


@dataclass
class Meeting:
    video_id: str          # e.g. "x185811"
    title: str             # e.g. "Council Briefing 4/13/26"
    date: str              # e.g. "4/13/2026"
    duration: str          # e.g. "50:14"
    slug: str              # e.g. "brief_041326_2012623" (the mp4 basename)
    mp4_url: str
    srt_url: Optional[str]
    thumbnail: str
    description: str       # plain-text description (agenda preview)
    committee: str         # inferred from the slug prefix ("brief", "gov", "lib", etc.)

    def to_dict(self) -> dict:
        return asdict(self)


# Map the slug prefixes we've seen to human-readable committee names.
# We'll auto-add unknown ones as they show up.
COMMITTEE_PREFIXES = {
    "brief": "Council Briefing",
    "fullcc": "City Council",
    "cc": "City Council",
    "gov": "Governance and Utilities Committee",
    "hous": "Housing and Human Services Committee",
    "lib": "Libraries, Education, and Neighborhoods Committee",
    "parks": "Parks and Environment Committee",
    "selcomp": "Select Committee on Comprehensive Plan",
    "safe": "Public Safety Committee",
    "fin": "Finance and Economic Development Committee",
    "transpo": "Transportation Committee",
    "selfed": "Select Committee on Federal Funding",
    "landuse": "Land Use and Sustainability Committee",
    "budget": "Select Budget Committee",
    "sust": "Sustainability, City Light, and Arts Committee",
    "util": "Public Utilities Committee",
}


def _committee_from_slug(slug: str) -> str:
    # slug is like "brief_041326_2012623" or "selcomp_031926_2162603"
    prefix = slug.split("_", 1)[0]
    return COMMITTEE_PREFIXES.get(prefix, prefix.capitalize())


def _build_srt_url(slug: str, date_str: str) -> Optional[str]:
    """The SRT is at documents/{SeattleChannel|seattlechannel}/closedcaption/<year>/<slug>.srt.

    The year in the path matches the meeting year. We accept either capitalization —
    the server matches case-insensitively on macOS/Windows but the archive mixes both.
    """
    year_match = re.search(r"/(\d{4})/", date_str) or re.search(r"(\d{4})", date_str)
    year = year_match.group(1) if year_match else str(time.localtime().tm_year)
    return f"{CAPTION_BASE}/documents/seattlechannel/closedcaption/{year}/{slug}.srt"


def scrape_meetings(limit: int = 20) -> list[Meeting]:
    """Fetch the videos index and parse `loadJWPlayer7(...)` onclick handlers.

    The calls live inside HTML attributes with nested quoting. Rather than fight
    the unescaped form, we parse per-call by locating each `loadJWPlayer7(` and
    scanning forward for the fields in the *encoded* HTML source.
    """
    resp = requests.get(VIDEOS_INDEX_URL, timeout=30)
    resp.raise_for_status()
    raw = resp.text  # keep HTML-encoded so &quot; still delimits the desc blob

    meetings: list[Meeting] = []
    seen_ids: set[str] = set()

    for match in re.finditer(r"loadJWPlayer7\(", raw):
        start = match.end()
        # Grab a generous chunk after the opening paren
        chunk = raw[start:start + 3000]

        # 1. mp4 url — first &#39;...&#39; with .mp4
        mp4_match = re.search(r"&#39;(//video\.seattle\.gov/media/[^&]+\.mp4)&#39;", chunk)
        if not mp4_match:
            continue
        mp4 = "https:" + mp4_match.group(1)
        slug_match = re.search(r"/([^/]+)\.mp4$", mp4)
        slug = slug_match.group(1) if slug_match else None
        if not slug:
            continue

        # 2. video_id — &#39;x<digits>&#39; later in the chunk (after 'false,')
        vid_match = re.search(r"false\s*,\s*&#39;(x\d+)&#39;", chunk)
        if not vid_match:
            continue
        vid = vid_match.group(1)
        if vid in seen_ids:
            continue
        seen_ids.add(vid)

        # 3. title, date, duration — the three &#39;...&#39; pairs *after* the &quot;...&quot; desc
        # Find the &quot;...&quot; block and parse fields after it.
        desc_match = re.search(r"&quot;(.+?)&quot;", chunk, re.DOTALL)
        if desc_match:
            after = chunk[desc_match.end():]
        else:
            after = chunk

        str_fields = re.findall(r"&#39;([^&]*)&#39;", after)
        title = str_fields[0] if len(str_fields) > 0 else ""
        date = str_fields[1] if len(str_fields) > 1 else ""
        dur = str_fields[2] if len(str_fields) > 2 else ""

        # 4. SRT — find inside desc
        srt_url = None
        if desc_match:
            desc_raw = desc_match.group(1)
            srt_match = re.search(
                r"documents/[Ss]eattle[Cc]hannel/closedcaption/\d{4}/[^&\"\s]+\.srt",
                desc_raw,
            )
            if srt_match:
                srt_url = f"{CAPTION_BASE}/{srt_match.group(0)}"
        if not srt_url:
            srt_url = _build_srt_url(slug, date)

        desc_text = _strip_html(html.unescape(desc_match.group(1))) if desc_match else ""

        # Thumbnail — second &#39;...&#39; arg (images//...)
        thumb_match = re.search(r"&#39;(images//[^&]+)&#39;", chunk)
        thumbnail = thumb_match.group(1) if thumb_match else ""

        meetings.append(Meeting(
            video_id=vid,
            title=title.strip(),
            date=date.strip(),
            duration=dur.strip(),
            slug=slug,
            mp4_url=mp4,
            srt_url=srt_url,
            thumbnail=thumbnail,
            description=desc_text,
            committee=_committee_from_slug(slug),
        ))
        if len(meetings) >= limit:
            break

    return meetings


def _strip_html(s: str) -> str:
    """Lightweight HTML-to-text for the description blob."""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return s.strip()


if __name__ == "__main__":
    meetings = scrape_meetings(limit=10)
    for m in meetings:
        print(f"{m.video_id}  {m.title}  ({m.date}, {m.duration})")
        print(f"   committee: {m.committee}")
        print(f"   mp4: {m.mp4_url}")
        print(f"   srt: {m.srt_url}")
        print()
