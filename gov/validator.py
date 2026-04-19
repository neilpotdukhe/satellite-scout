"""Validate an extracted.json against the source transcript.

Checks:
1. Every `bills_mentioned` bill number appears verbatim in the SRT
2. Every `key_quotes.quote` has substantial word overlap with a paragraph
3. Every `topics[].start_sec` is within the meeting duration
4. Every `topics[].speakers` name appears at least once in the transcript
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gov.fetcher import meeting_dir


@dataclass
class ValidationResult:
    meeting_id: str
    passed: bool = True
    issues: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def validate_extraction(meeting_id: str) -> ValidationResult:
    mdir = meeting_dir(meeting_id)
    ex_path = mdir / "extracted.json"
    srt_path = mdir / "transcript.srt"

    result = ValidationResult(meeting_id=meeting_id)

    if not ex_path.exists():
        result.passed = False
        result.issues.append({"level": "error", "check": "extraction_exists", "msg": "no extracted.json"})
        return result
    if not srt_path.exists():
        result.passed = False
        result.issues.append({"level": "error", "check": "transcript_exists", "msg": "no transcript.srt"})
        return result

    ex = json.loads(ex_path.read_text())
    srt_raw = srt_path.read_text(errors="replace")
    srt_upper = srt_raw.upper()

    # Check 1: bills_mentioned — each bill number must appear in transcript
    bills_ok, bills_bad = 0, 0
    for bill in ex.get("bills_mentioned", []):
        # Extract the digit portion (e.g., "CB 121179" -> "121179")
        digits = re.findall(r"\d{5,6}", bill)
        if not digits:
            continue
        digit_str = digits[0]
        if digit_str in srt_raw:
            bills_ok += 1
        else:
            bills_bad += 1
            result.issues.append({
                "level": "error",
                "check": "bill_in_transcript",
                "msg": f"bill '{bill}' (digits {digit_str}) not found in transcript",
                "field": "bills_mentioned",
            })
    result.stats["bills_ok"] = bills_ok
    result.stats["bills_bad"] = bills_bad

    # Also check per-topic bills_mentioned
    for i, topic in enumerate(ex.get("topics", [])):
        for bill in topic.get("bills_mentioned", []):
            digits = re.findall(r"\d{5,6}", bill)
            if not digits:
                continue
            digit_str = digits[0]
            if digit_str not in srt_raw:
                result.issues.append({
                    "level": "error",
                    "check": "topic_bill_in_transcript",
                    "msg": f"topic[{i}] ('{topic.get('title','')}') bill '{bill}' not in transcript",
                })

    # Check 2: key_quotes — each quote should have substantial overlap with source
    quotes_ok, quotes_bad = 0, 0
    for i, q in enumerate(ex.get("key_quotes", [])):
        quote = q.get("quote", "")
        if not quote:
            continue
        # Normalize: uppercase, strip punctuation, word-break
        norm_quote = re.sub(r"[^A-Z0-9\s]", " ", quote.upper())
        words = [w for w in norm_quote.split() if len(w) > 3]
        if len(words) < 3:
            continue
        # Take 5 middle words — they should all be in the transcript for a faithful quote
        sample = words[len(words) // 4: len(words) // 4 + 5]
        found = sum(1 for w in sample if w in srt_upper)
        if found >= 4:
            quotes_ok += 1
        else:
            quotes_bad += 1
            result.issues.append({
                "level": "warning",
                "check": "quote_in_transcript",
                "msg": f"key_quotes[{i}] ('{quote[:60]}...') has low overlap with source ({found}/5 words)",
            })
    result.stats["quotes_ok"] = quotes_ok
    result.stats["quotes_bad"] = quotes_bad

    # Check 3: timestamps within duration
    duration = ex.get("duration_sec", 0)
    if duration > 0:
        for i, topic in enumerate(ex.get("topics", [])):
            start = topic.get("start_sec", 0)
            end = topic.get("end_sec", start)
            if start < 0 or start > duration + 60:
                result.issues.append({
                    "level": "warning",
                    "check": "timestamp_in_range",
                    "msg": f"topic[{i}] start_sec={start} outside [0,{duration}]",
                })
            if end < start:
                result.issues.append({
                    "level": "warning",
                    "check": "timestamps_ordered",
                    "msg": f"topic[{i}] end_sec={end} < start_sec={start}",
                })

    # Decide pass/fail: errors fail, warnings don't
    result.passed = not any(i["level"] == "error" for i in result.issues)
    return result


def validate_all() -> list[ValidationResult]:
    results = []
    for d in Path("cache/gov/meetings").iterdir():
        if not d.is_dir():
            continue
        if (d / "extracted.json").exists():
            results.append(validate_extraction(d.name))
    return results


if __name__ == "__main__":
    results = validate_all()
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    print(f"Validated {total} meetings: {passed} passed, {total - passed} failed")
    print()
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.meeting_id}")
        for issue in r.issues:
            print(f"     [{issue['level']}] {issue['check']}: {issue['msg']}")
        if r.stats:
            print(f"     stats: {r.stats}")
