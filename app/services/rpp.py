"""REAPER RPP project file export service."""
from __future__ import annotations

import os
from pathlib import Path

from app.services.audio import get_duration_ffprobe


def create_simple_item(
    wav_path: str,
    position: float,
    length: float,
    soffs: float = 0.0,
    name: str = "",
    mute: int = 0,
    fadein: float = 0.005,
    fadeout: float = 0.005,
) -> str:
    """Create an RPP item block."""
    name_line = f"\t\t\tNAME \"{name}\"\n" if name else ""
    return (
        "\t\t<ITEM\n"
        f"\t\t\tPOSITION {position:.6f}\n"
        f"\t\t\tLENGTH {length:.6f}\n"
        f"\t\t\tSOFFS {soffs:.6f}\n"
        f"\t\t\tMUTE {mute}\n"
        f"\t\t\tFADEIN 1 {fadein:.6f} 0\n"
        f"\t\t\tFADEOUT 1 {fadeout:.6f} 0\n"
        f"{name_line}"
        "\t\t\t<SOURCE WAVE\n"
        f"\t\t\t\tFILE \"{wav_path}\"\n"
        "\t\t\t>\n"
        "\t\t>\n"
    )


def create_track(items_str: str, name: str = "", vol: float = 1.0, mute: int = 0) -> str:
    """Wrap items in a track block."""
    name_line = f"\t\tNAME \"{name}\"\n" if name else ""
    return (
        f"\t<TRACK\n"
        f"{name_line}"
        f"\t\tVOLPAN {vol:.6f} 0 -1 -1 1\n"
        f"\t\tMUTESOLO {mute} 0 0\n"
        f"{items_str}"
        f"\t>\n"
    )


def create_marker(index: int, position: float, name: str, color: int = 0) -> str:
    """Create a REAPER marker line."""
    color_str = f" {color}" if color else ""
    return f"  MARKER {index} {position:.6f} \"{name}\"{color_str}\n"


def build_conformed_items(
    audio_path_map: dict[int, str],
    default_audio_path: str,
    segments: list[dict],
    conflicts: list[dict] | None = None,
) -> str:
    """Build conformed items from aligned segments.

    Each segment becomes a separate item positioned according to its alignment.
    Segments marked as 'needs_edit' are muted.

    Args:
        audio_path_map: Mapping of audio_file_id -> file path.
        default_audio_path: Fallback path when segment has no audio_file_id.
        segments: Aligned segment dicts (must include 'audio_file_id').
        conflicts: Optional conflict dicts.
    """
    conflict_map = {}
    if conflicts:
        for c in conflicts:
            conflict_map[c.get("segment_index", -1)] = c

    items = []
    position = 0.0

    for seg in segments:
        if seg.get("alignment") == "missing":
            continue  # skip missing words - no audio

        start = seg.get("start_time", 0)
        end = seg.get("end_time", 0)
        length = end - start

        if length <= 0:
            continue

        # Look up the correct audio file for this segment
        seg_audio_id = seg.get("audio_file_id")
        audio_path = audio_path_map.get(seg_audio_id, default_audio_path) if seg_audio_id else default_audio_path

        seg_idx = seg.get("segment_index", 0)
        conflict = conflict_map.get(seg_idx)
        mute = 1 if conflict and conflict.get("status") == "needs_edit" else 0

        name = seg.get("text", "") or seg.get("expected_text", "")
        items.append(create_simple_item(
            audio_path, position, length, soffs=start, name=name, mute=mute
        ))
        position += length

    return "".join(items)


def export_rpp(
    output_path: str,
    project_title: str,
    audio_files: list[dict],
    segments: list[dict],
    conflicts: list[dict] | None = None,
    sample_rate: int = 44100,
) -> str:
    """Generate a complete REAPER .rpp project file.

    Args:
        output_path: Where to write the .rpp file
        project_title: Project name for the file
        audio_files: List of dicts with 'path', 'filename', 'duration'
        segments: Aligned segments from the alignment engine
        conflicts: Optional conflict records
        sample_rate: Audio sample rate

    Returns:
        Path to the created .rpp file
    """
    tracks = []
    markers = []
    marker_idx = 1

    # Build audio path lookup: audio_file_id -> filesystem path
    audio_path_map = {}
    for af in audio_files:
        af_id = af.get("audio_file_id")
        if af_id is not None:
            audio_path_map[af_id] = af.get("path", "")
    default_audio = audio_files[0].get("path", "") if audio_files else ""

    # Build conformed position map so markers and reference tracks
    # can be placed at the correct timeline positions.
    conformed_pos_map = {}
    position = 0.0
    for seg in segments:
        if seg.get("alignment") == "missing":
            continue
        start = seg.get("start_time", 0)
        end = seg.get("end_time", 0)
        length = end - start
        if length <= 0:
            continue
        seg_idx = seg.get("segment_index", 0)
        conformed_pos_map[seg_idx] = position
        position += length

    # Create conflict markers using conformed timeline positions
    if conflicts:
        for c in conflicts:
            seg_idx = c.get("segment_index", 0)
            if seg_idx in conformed_pos_map:
                pos = conformed_pos_map[seg_idx]
                ctype = c.get("conflict_type", "issue")
                status = c.get("status", "pending")
                detected = c.get("detected_text", "")
                expected = c.get("expected_text", "")
                label = f"[{ctype}:{status}]"
                if detected and expected:
                    label += f" {detected} -> {expected}"
                elif detected:
                    label += f" {detected}"
                elif expected:
                    label += f" (missing: {expected})"

                # Color coding: red for needs_edit, yellow for pending, green for ok
                color = _marker_color(status)
                markers.append(create_marker(marker_idx, pos, label, color))
                marker_idx += 1

    # Track 1: Reference (full unedited audio files, laid out sequentially)
    ref_offset = 0.0
    for i, af in enumerate(audio_files):
        path = af.get("path", "")
        duration = af.get("duration", 0)
        if duration <= 0 and os.path.exists(path):
            duration = get_duration_ffprobe(path)
        if duration > 0:
            items = create_simple_item(path, ref_offset, duration, name=af.get("filename", ""))
            tracks.append(create_track(
                items, name=f"Reference - {af.get('filename', f'Audio {i+1}')}", mute=1
            ))
            ref_offset += duration

    # Track 2: Conformed edit (segments assembled in order)
    if segments and audio_files:
        conformed_items = build_conformed_items(
            audio_path_map, default_audio, segments, conflicts
        )
        if conformed_items:
            tracks.append(create_track(conformed_items, name="Conformed Edit"))

    # Track 3: Flagged segments (needs_edit items isolated)
    if conflicts:
        flagged_items = []
        position = 0.0
        for c in conflicts:
            if c.get("status") != "needs_edit":
                continue
            seg_idx = c.get("segment_index", 0)
            matching = [s for s in segments if s.get("segment_index") == seg_idx]
            for seg in matching:
                start = seg.get("start_time", 0)
                end = seg.get("end_time", 0)
                length = end - start
                if length > 0:
                    seg_audio_id = seg.get("audio_file_id")
                    audio_path = audio_path_map.get(seg_audio_id, default_audio) if seg_audio_id else default_audio
                    flagged_items.append(create_simple_item(
                        audio_path, position, length, soffs=start,
                        name=f"[EDIT] {seg.get('text', '')}",
                    ))
                    position += length + 0.1  # small gap between flagged items

        if flagged_items:
            tracks.append(create_track(
                "".join(flagged_items), name="Flagged - Needs Edit", mute=1
            ))

    # Build project
    output_stem = Path(output_path).stem
    configs = (
        f'  RENDER_FILE "{output_stem}_vR1.wav"\n'
        f"  RENDER_FMT 0 1 {sample_rate}\n"
        "  RENDER_1X 0\n"
        "  RENDER_RANGE 1 0 0 18 1000\n"
        "  RENDER_RESAMPLE 3 0 1\n"
        "  RENDER_ADDTOPROJ 0\n"
        "  RENDER_STEMS 0\n"
        "  RENDER_DITHER 0\n"
        "  TIMELOCKMODE 0\n"
        "  TEMPOENVLOCKMODE 0\n"
        "  ITEMMIX 1\n"
        "  DEFPITCHMODE 589824 0\n"
        "  TAKELANE 1\n"
        f"  SAMPLERATE {sample_rate} 0 0\n"
        "  <RENDER_CFG\n  ZXZhdxgAAA==\n  >\n"
    )

    markers_str = "".join(markers)
    project = "<REAPER_PROJECT\n" + configs + markers_str + "".join(tracks) + ">"

    with open(output_path, "w") as f:
        f.write(project)

    return output_path


def _marker_color(status: str) -> int:
    """Return REAPER color int for conflict status."""
    colors = {
        "needs_edit": 33554687,   # red-ish
        "pending": 33488896,       # yellow-ish
        "ok": 16842752,            # green-ish
    }
    return colors.get(status, 0)
