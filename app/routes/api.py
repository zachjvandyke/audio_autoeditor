import os

from flask import Blueprint, current_app, jsonify, request, send_file
from app import db
from app.models import AlignmentSegment, AudioFile, Conflict, Project, Take

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
