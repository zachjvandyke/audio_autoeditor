"""Conflict detection service - identifies discrepancies between audio and manuscript.

Consecutive words with the same conflict type (e.g. three added words "as he
said") are grouped into a single conflict record instead of producing one
per word.
"""
from __future__ import annotations


# Map alignment values to the conflict type they produce.
_ALIGNMENT_TO_CONFLICT = {
    "mismatch": "misread",
    "missing": "missing_word",
    "extra": "extra_word",
}


def detect_conflicts(aligned_segments: list[dict]) -> list[dict]:
    """Analyze aligned segments and generate conflict records.

    Consecutive segments sharing the same conflict type are merged into one
    record so that, for example, three consecutive extra words produce a
    single "extra_word" conflict with the full phrase.

    Returns list of conflict dicts ready for database insertion.
    """
    conflicts: list[dict] = []

    # Collect per-segment conflict info, preserving order.
    raw: list[dict | None] = []
    for seg in aligned_segments:
        alignment = seg.get("alignment", "match")
        confidence = seg.get("confidence", 1.0)

        conflict_type = _ALIGNMENT_TO_CONFLICT.get(alignment)
        if conflict_type is None and alignment == "match" and confidence < 0.6:
            conflict_type = "low_confidence"

        if conflict_type is not None:
            raw.append({
                "segment_index": seg["segment_index"],
                "conflict_type": conflict_type,
                "detected_text": seg.get("text", ""),
                "expected_text": seg.get("expected_text", ""),
            })
        else:
            # Non-conflict segment breaks any active run.
            raw.append(None)

    # Group consecutive entries that share the same conflict_type.
    conflicts = _group_consecutive(raw)

    # Detect long pauses (these are never grouped).
    _detect_pauses(aligned_segments, conflicts)

    return conflicts


def _group_consecutive(raw: list[dict | None]) -> list[dict]:
    """Merge consecutive raw conflict entries of the same type into one record."""
    groups: list[dict] = []
    current: dict | None = None

    for entry in raw:
        if entry is None:
            # A match / non-conflict breaks the run.
            if current is not None:
                groups.append(current)
                current = None
            continue

        if (
            current is not None
            and current["conflict_type"] == entry["conflict_type"]
        ):
            # Extend the current group.
            if entry["detected_text"]:
                current["detected_text"] += (
                    " " + entry["detected_text"] if current["detected_text"] else entry["detected_text"]
                )
            if entry["expected_text"]:
                current["expected_text"] += (
                    " " + entry["expected_text"] if current["expected_text"] else entry["expected_text"]
                )
        else:
            # Start a new group (flush previous if any).
            if current is not None:
                groups.append(current)
            current = {
                "segment_index": entry["segment_index"],
                "conflict_type": entry["conflict_type"],
                "status": "pending",
                "detected_text": entry["detected_text"],
                "expected_text": entry["expected_text"],
            }

    if current is not None:
        groups.append(current)

    return groups


def _detect_pauses(segments: list[dict], conflicts: list[dict], threshold: float = 2.0):
    """Detect unusually long pauses between segments."""
    for i in range(1, len(segments)):
        prev_end = segments[i - 1].get("end_time", 0)
        curr_start = segments[i].get("start_time", 0)
        gap = curr_start - prev_end

        if gap > threshold:
            conflicts.append({
                "segment_index": segments[i]["segment_index"],
                "conflict_type": "pause",
                "status": "pending",
                "detected_text": f"[{gap:.1f}s pause]",
                "expected_text": "",
            })
