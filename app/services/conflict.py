"""Conflict detection service - identifies discrepancies between audio and manuscript."""


def detect_conflicts(aligned_segments: list[dict]) -> list[dict]:
    """Analyze aligned segments and generate conflict records.

    Returns list of conflict dicts ready for database insertion.
    """
    conflicts = []

    for seg in aligned_segments:
        alignment = seg.get("alignment", "match")
        confidence = seg.get("confidence", 1.0)

        if alignment == "mismatch":
            conflicts.append({
                "segment_index": seg["segment_index"],
                "conflict_type": "misread",
                "status": "pending",
                "detected_text": seg["text"],
                "expected_text": seg["expected_text"],
            })

        elif alignment == "missing":
            conflicts.append({
                "segment_index": seg["segment_index"],
                "conflict_type": "missing_word",
                "status": "pending",
                "detected_text": "",
                "expected_text": seg["expected_text"],
            })

        elif alignment == "extra":
            conflicts.append({
                "segment_index": seg["segment_index"],
                "conflict_type": "extra_word",
                "status": "pending",
                "detected_text": seg["text"],
                "expected_text": "",
            })

        elif alignment == "match" and confidence < 0.6:
            conflicts.append({
                "segment_index": seg["segment_index"],
                "conflict_type": "low_confidence",
                "status": "pending",
                "detected_text": seg["text"],
                "expected_text": seg["expected_text"],
            })

    # Detect long pauses
    _detect_pauses(aligned_segments, conflicts)

    return conflicts


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
