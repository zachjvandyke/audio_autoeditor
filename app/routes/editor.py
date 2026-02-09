import os

from flask import (
    Blueprint, current_app, redirect, render_template, send_file, url_for, flash,
)
from app.models import (
    AlignmentSegment, AudioFile, Conflict, ManuscriptSection, Project, Take,
)
from app.services.rpp import export_rpp

editor_bp = Blueprint("editor", __name__)


@editor_bp.route("/<int:project_id>")
def edit(project_id):
    project = Project.query.get_or_404(project_id)

    # Get manuscript sections (chapters with paragraphs)
    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    paragraphs = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="paragraph"
    ).order_by(ManuscriptSection.section_index).all()

    # Get alignment segments
    segments = AlignmentSegment.query.filter_by(
        project_id=project_id
    ).order_by(AlignmentSegment.segment_index).all()

    # Get conflicts
    conflicts = Conflict.query.filter_by(
        project_id=project_id
    ).order_by(Conflict.id).all()

    # Get audio files
    audio_files = AudioFile.query.filter_by(project_id=project_id).all()

    # Build segment-to-conflict and segment-to-takes maps
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

    # Gather data
    audio_files = AudioFile.query.filter_by(project_id=project_id).all()
    segments = AlignmentSegment.query.filter_by(
        project_id=project_id
    ).order_by(AlignmentSegment.segment_index).all()
    conflicts = Conflict.query.filter_by(project_id=project_id).all()

    af_data = []
    for af in audio_files:
        af_data.append({
            "path": os.path.join(upload_folder, af.filename),
            "filename": af.original_filename,
            "duration": af.duration,
        })

    seg_data = []
    for s in segments:
        seg_data.append({
            "text": s.text,
            "expected_text": s.expected_text or "",
            "start_time": s.start_time,
            "end_time": s.end_time,
            "confidence": s.confidence,
            "segment_type": s.segment_type,
            "segment_index": s.segment_index,
            "alignment": _infer_alignment(s),
        })

    conflict_data = []
    for c in conflicts:
        conflict_data.append({
            "segment_index": c.segment.segment_index if c.segment else 0,
            "conflict_type": c.conflict_type,
            "status": c.status,
            "detected_text": c.detected_text or "",
            "expected_text": c.expected_text or "",
        })

    # Generate RPP
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in project.title)
    output_path = os.path.join(export_folder, f"{safe_title}.rpp")

    sample_rate = audio_files[0].sample_rate if audio_files else 44100
    export_rpp(output_path, project.title, af_data, seg_data, conflict_data, sample_rate)

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
