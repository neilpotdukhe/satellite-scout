"""Download + cache SRT captions and agenda PDFs for a meeting."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import requests

from gov.scraper import Meeting


MEETINGS_DIR = Path("cache/gov/meetings")


@dataclass
class TranscriptSegment:
    idx: int
    start_sec: float
    end_sec: float
    text: str


def meeting_dir(meeting_id: str) -> Path:
    """Return the on-disk cache dir for a meeting (by video_id or slug)."""
    d = MEETINGS_DIR / meeting_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_metadata(meeting: Meeting):
    mdir = meeting_dir(meeting.video_id)
    (mdir / "metadata.json").write_text(json.dumps(meeting.to_dict(), indent=2))


def load_metadata(meeting_id: str) -> Optional[dict]:
    p = meeting_dir(meeting_id) / "metadata.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def fetch_srt(meeting: Meeting) -> Path:
    """Download the SRT for a meeting, cache it, return the local path."""
    if not meeting.srt_url:
        raise ValueError(f"meeting {meeting.video_id} has no srt_url")
    mdir = meeting_dir(meeting.video_id)
    srt_path = mdir / "transcript.srt"
    if srt_path.exists() and srt_path.stat().st_size > 0:
        return srt_path

    r = requests.get(meeting.srt_url, timeout=60)
    r.raise_for_status()
    srt_path.write_bytes(r.content)
    return srt_path


def _parse_srt_timecode(tc: str) -> float:
    """Convert an SRT timecode '00:00:29,796' into seconds."""
    h, m, rest = tc.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(srt_path: Path) -> list[TranscriptSegment]:
    """Parse an SRT file into a list of TranscriptSegments."""
    raw = srt_path.read_text(errors="replace")
    segments: list[TranscriptSegment] = []
    # SRT blocks are separated by blank lines. Each block:
    #   idx
    #   start --> end
    #   text line(s)
    block_pat = re.compile(
        r"(\d+)\s*\n"
        r"(\d\d:\d\d:\d\d,\d\d\d)\s+-->\s+(\d\d:\d\d:\d\d,\d\d\d)\s*\n"
        r"((?:.+\n?)+?)"
        r"(?:\n|$)",
    )
    for m in block_pat.finditer(raw):
        idx = int(m.group(1))
        start = _parse_srt_timecode(m.group(2))
        end = _parse_srt_timecode(m.group(3))
        text = m.group(4).strip().replace("\n", " ")
        # Cleanup: unescape &gt; and strip speaker markers
        text = text.replace("&gt;&gt;", "»").replace("&gt;", ">").replace("&lt;", "<")
        segments.append(TranscriptSegment(idx=idx, start_sec=start, end_sec=end, text=text))
    return segments


def segments_to_paragraphs(segments: list[TranscriptSegment],
                            max_gap_sec: float = 2.5) -> list[dict]:
    """Group adjacent segments into readable paragraphs. A new paragraph starts
    when the speaker marker `»` appears or there's a gap > max_gap_sec.

    Returns a list of {start_sec, end_sec, text} dicts that are much more
    readable than the ~1-line-per-second SRT format.
    """
    paragraphs: list[dict] = []
    cur = {"start_sec": None, "end_sec": None, "text": ""}

    for seg in segments:
        is_new = False
        if cur["start_sec"] is None:
            is_new = True
        elif seg.text.startswith("»"):
            is_new = True
        elif seg.start_sec - cur["end_sec"] > max_gap_sec:
            is_new = True

        if is_new:
            if cur["text"].strip():
                paragraphs.append({
                    "start_sec": cur["start_sec"],
                    "end_sec": cur["end_sec"],
                    "text": cur["text"].strip(),
                })
            cur = {"start_sec": seg.start_sec, "end_sec": seg.end_sec, "text": seg.text}
        else:
            cur["end_sec"] = seg.end_sec
            cur["text"] += " " + seg.text

    if cur["text"].strip():
        paragraphs.append({
            "start_sec": cur["start_sec"],
            "end_sec": cur["end_sec"],
            "text": cur["text"].strip(),
        })
    return paragraphs


def write_transcript_json(meeting: Meeting) -> Path:
    """Parse the cached SRT into structured transcript.json (segments + paragraphs)."""
    mdir = meeting_dir(meeting.video_id)
    srt_path = mdir / "transcript.srt"
    if not srt_path.exists():
        fetch_srt(meeting)

    segments = parse_srt(srt_path)
    paragraphs = segments_to_paragraphs(segments)

    out = {
        "meeting_id": meeting.video_id,
        "total_segments": len(segments),
        "total_paragraphs": len(paragraphs),
        "duration_sec": segments[-1].end_sec if segments else 0,
        "segments": [asdict(s) for s in segments],
        "paragraphs": paragraphs,
    }
    tpath = mdir / "transcript.json"
    tpath.write_text(json.dumps(out, indent=2))
    return tpath


def plain_text_transcript(meeting_id: str, max_chars: int = 120_000) -> str:
    """Get a readable plain-text version of the transcript for LLM input.

    Uses paragraphs with [MM:SS] timestamps. Truncates to max_chars if needed.
    """
    tpath = meeting_dir(meeting_id) / "transcript.json"
    if not tpath.exists():
        raise FileNotFoundError(tpath)
    data = json.loads(tpath.read_text())
    lines = []
    for p in data["paragraphs"]:
        ts = int(p["start_sec"])
        mm, ss = ts // 60, ts % 60
        lines.append(f"[{mm:02d}:{ss:02d}] {p['text']}")
    joined = "\n".join(lines)
    if len(joined) > max_chars:
        joined = joined[:max_chars] + "\n\n[... truncated ...]"
    return joined
