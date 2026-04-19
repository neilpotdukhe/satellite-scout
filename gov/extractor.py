"""Claude-Code-driven extractor: transcript → structured meeting summary.

The extractor writes a `job.md` file in the meeting cache dir that describes
what Claude Code should do, then spawns `cc -p "/gov extract <meeting_id>"`
as a subprocess. The child process reads the transcript, thinks about it,
and writes `extracted.json` back to the cache dir.

Schema of extracted.json:

{
  "meeting_id": "x185811",
  "summary": "One-paragraph summary of what happened",
  "headline": "Short (<12-word) headline",
  "committee": "Council Briefing",
  "date": "4/13/2026",
  "duration_sec": 3003.5,
  "topics": [
    {
      "title": "Short topic title",
      "start_sec": 120,
      "end_sec": 340,
      "summary": "2-3 sentences on what was discussed",
      "importance": "high" | "medium" | "low",
      "bills_mentioned": ["CB 121187", ...],
      "speakers": ["Councilmember Foster", ...],
      "decisions": [
        {
          "action": "voted to / passed / referred / discussed",
          "description": "...",
          "vote": "unanimous" | "6-3" | null
        }
      ]
    }
  ],
  "key_quotes": [
    {"speaker": "...", "quote": "...", "start_sec": 1234, "context": "..."}
  ],
  "tags": ["housing", "zoning", "police", ...]
}
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from gov.fetcher import meeting_dir, plain_text_transcript


def spawn_extractor(meeting_id: str) -> Optional[int]:
    """Spawn `cc -p` to process a single meeting. Returns the child PID."""
    mdir = meeting_dir(meeting_id)
    log_path = mdir / "extractor.log"

    prompt = f"/gov extract {meeting_id}"

    try:
        log_file = open(log_path, "wb")
        proc = subprocess.Popen(
            [
                "claude",
                "--dangerously-skip-permissions",
                "-p",
                prompt,
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(Path.cwd()),
            env=os.environ.copy(),
            start_new_session=True,
        )
        return proc.pid
    except Exception as e:
        print(f"[spawn_extractor] error: {e}")
        return None


def is_extracted(meeting_id: str) -> bool:
    return (meeting_dir(meeting_id) / "extracted.json").exists()


def load_extracted(meeting_id: str) -> Optional[dict]:
    p = meeting_dir(meeting_id) / "extracted.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def save_extracted(meeting_id: str, data: dict):
    p = meeting_dir(meeting_id) / "extracted.json"
    p.write_text(json.dumps(data, indent=2))
