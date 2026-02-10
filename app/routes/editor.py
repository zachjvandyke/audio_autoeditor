import os

from flask import (
    Blueprint, current_app, redirect, render_template, request, send_file, url_for, flash,
)
from app.models import (
    AlignmentSegment, AudioFile, Conflict, ManuscriptSection, Project, Take,
)
from app.services.rpp import export_rpp

editor_bp = Blueprint("editor", __name__)

# SQLite max variable limit is 999; use 500 per batch to leave room for other params.
_BATCH_SIZE = 500


def _batched_in_query(query_fn, ids):
    """Execute a query in batches to avoid SQLite's 999 variable limit.

    ``query_fn`` is a callable that accepts a collection of IDs and returns a
    SQLAlchemy query object.  The results from each batch are combined into a
    single list.
    """
    ids = list(ids)
    if not ids:
        return []
    results = []
    for i in range(0, len(ids), _BATCH_SIZE):
        batch = ids[i : i + _BATCH_SIZE]
        results.extend(query_fn(batch).all())
    return results


@editor_bp.route("/<int:project_id>")
def edit(project_id):
    project = Project.query.get_or_404(project_id)

    # Get all chapters
    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    # Determine active chapter (from query param, or first chapter)
    active_chapter_id = request.args.get("chapter", type=int)
    active_chapter = None
    if active_chapter_id:
        active_chapter = ManuscriptSection.query.get(active_chapter_id)
    if not active_chapter and chapters:
        active_chapter = chapters[0]

    # Get paragraphs for the active chapter (or all if no chapters)
    if active_chapter:
        paragraphs = ManuscriptSection.query.filter_by(
            parent_id=active_chapter.id, section_type="paragraph"
        ).order_by(ManuscriptSection.section_index).all()
    else:
        paragraphs = ManuscriptSection.query.filter_by(
            project_id=project_id, section_type="paragraph"
        ).order_by(ManuscriptSection.section_index).all()

    # Get paragraph IDs for filtering segments/conflicts
    para_ids = {p.id for p in paragraphs}

    # Get alignment segments for these paragraphs
    if para_ids:
        segments = _batched_in_query(
            lambda batch: AlignmentSegment.query.filter(
                AlignmentSegment.project_id == project_id,
                AlignmentSegment.manuscript_section_id.in_(batch),
            ).order_by(AlignmentSegment.segment_index),
            para_ids,
        )
    else:
        segments = AlignmentSegment.query.filter_by(
            project_id=project_id
        ).order_by(AlignmentSegment.segment_index).all()

    # Get segment IDs for filtering conflicts
    seg_ids = {s.id for s in segments}

    # Get conflicts for these segments
    if seg_ids:
        conflicts = _batched_in_query(
            lambda batch: Conflict.query.filter(
                Conflict.project_id == project_id,
                Conflict.segment_id.in_(batch),
            ).order_by(Conflict.id),
            seg_ids,
        )
    else:
        conflicts = Conflict.query.filter_by(
            project_id=project_id
        ).order_by(Conflict.id).all()

    # Get audio files for this chapter
    if active_chapter:
        audio_files = AudioFile.query.filter_by(
            project_id=project_id, chapter_id=active_chapter.id
        ).order_by(AudioFile.sort_order).all()
        # Fallback to all audio if none assigned to this chapter
        if not audio_files:
            audio_files = AudioFile.query.filter_by(
                project_id=project_id
            ).order_by(AudioFile.sort_order).all()
    else:
        audio_files = AudioFile.query.filter_by(
            project_id=project_id
        ).order_by(AudioFile.sort_order).all()

    # Build per-chapter conflict stats for the tab badges
    chapter_stats = {}
    for ch in chapters:
        ch_para_ids = {p.id for p in ch.children}
        if ch_para_ids:
            ch_seg_ids = {
                s.id for s in _batched_in_query(
                    lambda batch: AlignmentSegment.query.filter(
                        AlignmentSegment.project_id == project_id,
                        AlignmentSegment.manuscript_section_id.in_(batch),
                    ),
                    ch_para_ids,
                )
            }
            if ch_seg_ids:
                ch_conflicts = _batched_in_query(
                    lambda batch: Conflict.query.filter(
                        Conflict.project_id == project_id,
                        Conflict.segment_id.in_(batch),
                    ),
                    ch_seg_ids,
                )
                total = len(ch_conflicts)
                pending = sum(1 for c in ch_conflicts if c.status == "pending")
                chapter_stats[ch.id] = {"total": total, "pending": pending}

    # Build maps
    conflict_map = {}
    for c in conflicts:
        conflict_map.setdefault(c.segment_id, []).append(c)

    take_map = {}
    for seg in segments:
        takes = Take.query.filter_by(segment_id=seg.id).order_by(Take.take_number).all()
        if len(takes) > 1:
            take_map[seg.id] = takes

    return render_template(
        "editor.html",
        project=project,
        chapters=chapters,
        active_chapter=active_chapter,
        chapter_stats=chapter_stats,
        paragraphs=paragraphs,
        segments=segments,
        conflicts=conflicts,
        conflict_map=conflict_map,
        take_map=take_map,
        audio_files=audio_files,
    )


@editor_bp.route("/<int:project_id>/export")
def export(project_id):
    project = Project.query.get_or_404(project_id)
    export_folder = current_app.config["EXPORT_FOLDER"]
    upload_folder = current_app.config["UPLOAD_FOLDER"]

    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    # Gather all data organized by chapter
    all_af_data = []
    all_seg_data = []
    all_conflict_data = []

    for ch in chapters:
        ch_para_ids = {p.id for p in ch.children}
        ch_audio = AudioFile.query.filter_by(
            project_id=project_id, chapter_id=ch.id
        ).order_by(AudioFile.sort_order).all()

        for af in ch_audio:
            all_af_data.append({
                "audio_file_id": af.id,
                "path": os.path.join(upload_folder, af.filename),
                "filename": af.original_filename,
                "duration": af.duration,
                "chapter": ch.text_content,
            })

        if ch_para_ids:
            ch_segments = _batched_in_query(
                lambda batch: AlignmentSegment.query.filter(
                    AlignmentSegment.project_id == project_id,
                    AlignmentSegment.manuscript_section_id.in_(batch),
                ).order_by(AlignmentSegment.segment_index),
                ch_para_ids,
            )

            ch_seg_ids = {s.id for s in ch_segments}

            for s in ch_segments:
                all_seg_data.append({
                    "text": s.text,
                    "expected_text": s.expected_text or "",
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "confidence": s.confidence,
                    "segment_type": s.segment_type,
                    "segment_index": s.segment_index,
                    "alignment": _infer_alignment(s),
                    "audio_file_id": s.audio_file_id,
                    "chapter": ch.text_content,
                })

            if ch_seg_ids:
                ch_conflicts = _batched_in_query(
                    lambda batch: Conflict.query.filter(
                        Conflict.project_id == project_id,
                        Conflict.segment_id.in_(batch),
                    ),
                    ch_seg_ids,
                )
                for c in ch_conflicts:
                    all_conflict_data.append({
                        "segment_index": c.segment.segment_index if c.segment else 0,
                        "conflict_type": c.conflict_type,
                        "status": c.status,
                        "detected_text": c.detected_text or "",
                        "expected_text": c.expected_text or "",
                        "chapter": ch.text_content,
                    })

    # Also include unassigned audio
    unassigned_audio = AudioFile.query.filter_by(
        project_id=project_id, chapter_id=None
    ).all()
    for af in unassigned_audio:
        all_af_data.append({
            "audio_file_id": af.id,
            "path": os.path.join(upload_folder, af.filename),
            "filename": af.original_filename,
            "duration": af.duration,
            "chapter": "Unassigned",
        })

    # Generate RPP
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in project.title)
    output_path = os.path.join(export_folder, f"{safe_title}.rpp")

    audio_files = AudioFile.query.filter_by(project_id=project_id).all()
    sample_rate = audio_files[0].sample_rate if audio_files else 44100
    export_rpp(
        output_path, project.title, all_af_data, all_seg_data,
        all_conflict_data, sample_rate,
    )

    project.status = "exported"
    from app import db
    db.session.commit()

    return send_file(output_path, as_attachment=True, download_name=f"{safe_title}.rpp")


def _infer_alignment(segment) -> str:
    """Infer alignment status from segment data."""
    if not segment.text and segment.expected_text:
        return "missing"
    if segment.text and not segment.expected_text:
        return "extra"
    if segment.text and segment.expected_text:
        t = segment.text.lower().strip()
        e = segment.expected_text.lower().strip()
        if t == e:
            return "match"
        return "mismatch"
    return "match"
