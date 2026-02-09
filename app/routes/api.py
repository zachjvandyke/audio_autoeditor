import os

from flask import Blueprint, current_app, jsonify, request, send_file
from app import db
from app.models import AlignmentSegment, AudioFile, Conflict, ManuscriptSection, Project, Take

api_bp = Blueprint("api", __name__)


@api_bp.route("/project/<int:project_id>/status")
def project_status(project_id):
    project = Project.query.get_or_404(project_id)
    return jsonify({
        "status": project.status,
        "conflict_stats": project.conflict_stats,
    })


@api_bp.route("/conflict/<int:conflict_id>/update", methods=["POST"])
def update_conflict(conflict_id):
    conflict = Conflict.query.get_or_404(conflict_id)
    data = request.get_json()

    if "status" in data:
        if data["status"] in ("pending", "ok", "needs_edit"):
            conflict.status = data["status"]

    if "notes" in data:
        conflict.notes = data["notes"]

    db.session.commit()

    # Return updated stats
    project = Project.query.get(conflict.project_id)
    return jsonify({
        "id": conflict.id,
        "status": conflict.status,
        "notes": conflict.notes,
        "conflict_stats": project.conflict_stats,
    })


@api_bp.route("/conflict/batch-update", methods=["POST"])
def batch_update_conflicts():
    data = request.get_json()
    conflict_ids = data.get("conflict_ids", [])
    new_status = data.get("status", "ok")

    if new_status not in ("pending", "ok", "needs_edit"):
        return jsonify({"error": "Invalid status"}), 400

    updated = 0
    project_id = None
    for cid in conflict_ids:
        conflict = Conflict.query.get(cid)
        if conflict:
            conflict.status = new_status
            project_id = conflict.project_id
            updated += 1

    db.session.commit()

    stats = {}
    if project_id:
        project = Project.query.get(project_id)
        stats = project.conflict_stats

    return jsonify({"updated": updated, "conflict_stats": stats})


@api_bp.route("/take/<int:take_id>/select", methods=["POST"])
def select_take(take_id):
    take = Take.query.get_or_404(take_id)

    # Deselect all other takes for this segment
    Take.query.filter_by(segment_id=take.segment_id).update({"is_selected": False})
    take.is_selected = True

    # Update the segment's timing to match selected take
    segment = AlignmentSegment.query.get(take.segment_id)
    if segment:
        segment.start_time = take.start_time
        segment.end_time = take.end_time
        segment.audio_file_id = take.audio_file_id

    db.session.commit()

    return jsonify({
        "id": take.id,
        "segment_id": take.segment_id,
        "is_selected": True,
    })


@api_bp.route("/audio/<int:audio_id>/serve")
def serve_audio(audio_id):
    audio = AudioFile.query.get_or_404(audio_id)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    filepath = os.path.join(upload_folder, audio.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404
    return send_file(filepath)


@api_bp.route("/audio/<int:audio_id>/segment")
def serve_audio_segment(audio_id):
    """Serve a portion of audio for playback of a specific segment."""
    audio = AudioFile.query.get_or_404(audio_id)
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    filepath = os.path.join(upload_folder, audio.filename)
    if not os.path.exists(filepath):
        return jsonify({"error": "File not found"}), 404

    # For now, serve the full file - the frontend handles seeking
    return send_file(filepath)


@api_bp.route("/project/<int:project_id>/segments")
def get_segments(project_id):
    segments = AlignmentSegment.query.filter_by(
        project_id=project_id
    ).order_by(AlignmentSegment.segment_index).all()

    return jsonify([{
        "id": s.id,
        "text": s.text,
        "expected_text": s.expected_text,
        "start_time": s.start_time,
        "end_time": s.end_time,
        "confidence": s.confidence,
        "segment_type": s.segment_type,
        "segment_index": s.segment_index,
        "audio_file_id": s.audio_file_id,
    } for s in segments])


@api_bp.route("/project/<int:project_id>/conflicts")
def get_conflicts(project_id):
    filter_type = request.args.get("type")
    filter_status = request.args.get("status")

    query = Conflict.query.filter_by(project_id=project_id)
    if filter_type:
        query = query.filter_by(conflict_type=filter_type)
    if filter_status:
        query = query.filter_by(status=filter_status)

    conflicts = query.order_by(Conflict.id).all()

    return jsonify([{
        "id": c.id,
        "segment_id": c.segment_id,
        "conflict_type": c.conflict_type,
        "status": c.status,
        "detected_text": c.detected_text,
        "expected_text": c.expected_text,
        "notes": c.notes,
    } for c in conflicts])


# === TOC Editing Endpoints ===

@api_bp.route("/project/<int:project_id>/chapters")
def get_chapters(project_id):
    """Get all chapters with boundary context for the setup page."""
    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    result = []
    for i, ch in enumerate(chapters):
        paras = ManuscriptSection.query.filter_by(
            parent_id=ch.id, section_type="paragraph"
        ).order_by(ManuscriptSection.section_index).all()

        # Boundary context: last para of previous chapter
        prev_context = ""
        if i > 0:
            prev_paras = ManuscriptSection.query.filter_by(
                parent_id=chapters[i - 1].id, section_type="paragraph"
            ).order_by(ManuscriptSection.section_index.desc()).first()
            if prev_paras:
                prev_context = prev_paras.text_content[:200]

        # Boundary context: first para of next chapter
        next_context = ""
        if i < len(chapters) - 1:
            next_paras = ManuscriptSection.query.filter_by(
                parent_id=chapters[i + 1].id, section_type="paragraph"
            ).order_by(ManuscriptSection.section_index).first()
            if next_paras:
                next_context = next_paras.text_content[:200]

        first_para = paras[0].text_content[:200] if paras else ""
        last_para = paras[-1].text_content[:200] if paras else ""

        result.append({
            "id": ch.id,
            "title": ch.text_content,
            "section_index": ch.section_index,
            "paragraph_count": len(paras),
            "processing_status": ch.processing_status,
            "first_paragraph": first_para,
            "last_paragraph": last_para,
            "prev_chapter_ending": prev_context,
            "next_chapter_beginning": next_context,
        })

    return jsonify(result)


@api_bp.route("/project/<int:project_id>/chapter/add", methods=["POST"])
def add_chapter(project_id):
    """Add a new chapter split at a given paragraph, or add an empty chapter."""
    Project.query.get_or_404(project_id)
    data = request.get_json()
    title = data.get("title", "New Chapter").strip()
    after_chapter_id = data.get("after_chapter_id")  # insert after this chapter
    split_paragraph_id = data.get("split_paragraph_id")  # split at this paragraph

    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    # Determine insert position
    if after_chapter_id:
        after = ManuscriptSection.query.get(after_chapter_id)
        new_index = after.section_index + 1 if after else len(chapters)
    else:
        new_index = len(chapters)

    # Shift subsequent chapters
    for ch in chapters:
        if ch.section_index >= new_index:
            ch.section_index += 1

    # Create the new chapter
    new_chapter = ManuscriptSection(
        project_id=project_id,
        section_type="chapter",
        section_index=new_index,
        text_content=title,
    )
    db.session.add(new_chapter)
    db.session.flush()

    # If splitting: move paragraphs from split_paragraph onward to the new chapter
    if split_paragraph_id and after_chapter_id:
        source_chapter = ManuscriptSection.query.get(after_chapter_id)
        if source_chapter:
            split_para = ManuscriptSection.query.get(split_paragraph_id)
            if split_para:
                paras_to_move = ManuscriptSection.query.filter(
                    ManuscriptSection.parent_id == source_chapter.id,
                    ManuscriptSection.section_type == "paragraph",
                    ManuscriptSection.section_index >= split_para.section_index,
                ).all()
                for idx, p in enumerate(paras_to_move):
                    p.parent_id = new_chapter.id
                    p.section_index = idx

    db.session.commit()
    return jsonify({"id": new_chapter.id, "title": title, "section_index": new_index})


@api_bp.route("/chapter/<int:chapter_id>/remove", methods=["POST"])
def remove_chapter(chapter_id):
    """Remove a chapter heading, merging its paragraphs into the previous chapter."""
    chapter = ManuscriptSection.query.get_or_404(chapter_id)
    project_id = chapter.project_id

    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    if len(chapters) <= 1:
        return jsonify({"error": "Cannot remove the only chapter"}), 400

    # Find previous chapter to merge into
    prev_chapter = None
    for ch in chapters:
        if ch.section_index < chapter.section_index:
            prev_chapter = ch

    # If no previous, merge into next
    next_chapter = None
    if not prev_chapter:
        for ch in chapters:
            if ch.section_index > chapter.section_index:
                next_chapter = ch
                break

    target = prev_chapter or next_chapter

    # Move paragraphs to target chapter
    if target:
        existing_paras = ManuscriptSection.query.filter_by(
            parent_id=target.id, section_type="paragraph"
        ).count()

        paras = ManuscriptSection.query.filter_by(
            parent_id=chapter.id, section_type="paragraph"
        ).order_by(ManuscriptSection.section_index).all()

        for idx, p in enumerate(paras):
            p.parent_id = target.id
            p.section_index = existing_paras + idx

    # Unlink audio files
    AudioFile.query.filter_by(chapter_id=chapter.id).update({"chapter_id": None})

    # Delete the chapter heading
    db.session.delete(chapter)

    # Re-index remaining chapters
    remaining = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()
    for idx, ch in enumerate(remaining):
        ch.section_index = idx

    db.session.commit()
    return jsonify({"removed": chapter_id, "merged_into": target.id if target else None})


@api_bp.route("/chapter/<int:chapter_id>/rename", methods=["POST"])
def rename_chapter(chapter_id):
    """Rename a chapter heading."""
    chapter = ManuscriptSection.query.get_or_404(chapter_id)
    data = request.get_json()
    new_title = data.get("title", "").strip()
    if not new_title:
        return jsonify({"error": "Title cannot be empty"}), 400

    chapter.text_content = new_title
    db.session.commit()
    return jsonify({"id": chapter.id, "title": new_title})


@api_bp.route("/chapter/<int:chapter_id>/paragraphs")
def get_chapter_paragraphs(chapter_id):
    """Get all paragraphs for a chapter, used by the split-point picker."""
    chapter = ManuscriptSection.query.get_or_404(chapter_id)
    paras = ManuscriptSection.query.filter_by(
        parent_id=chapter.id, section_type="paragraph"
    ).order_by(ManuscriptSection.section_index).all()

    return jsonify([{
        "id": p.id,
        "section_index": p.section_index,
        "text": p.text_content[:300],
        "full_length": len(p.text_content),
    } for p in paras])


@api_bp.route("/project/<int:project_id>/chapter-status")
def chapter_processing_status(project_id):
    """Get per-chapter processing status for polling."""
    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    project = Project.query.get_or_404(project_id)

    return jsonify({
        "project_status": project.status,
        "chapters": [{
            "id": ch.id,
            "title": ch.text_content,
            "processing_status": ch.processing_status,
        } for ch in chapters],
    })
