from __future__ import annotations

import os
import re
import threading

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, url_for,
)
from app import db
from app.models import (
    AlignmentSegment, AudioFile, Conflict, ManuscriptSection, Project, Take,
)
from app.services.alignment import (
    align_transcript_to_manuscript, detect_retakes, transcribe_audio,
)
from app.services.audio import allowed_file, get_duration_ffprobe, get_sample_rate, save_upload
from app.services.conflict import detect_conflicts
from app.services.manuscript import parse_manuscript

project_bp = Blueprint("project", __name__)


@project_bp.route("/new")
def new():
    return render_template("project_create.html")


@project_bp.route("/create", methods=["POST"])
def create():
    title = request.form.get("title", "").strip()
    manuscript_text = request.form.get("manuscript", "").strip()
    manuscript_file = request.files.get("manuscript_file")

    if not title:
        flash("Project title is required.", "error")
        return redirect(url_for("project.new"))

    # Get manuscript text from file upload or text input
    if manuscript_file and manuscript_file.filename:
        manuscript_text = manuscript_file.read().decode("utf-8", errors="replace")
    if not manuscript_text:
        flash("Manuscript text is required.", "error")
        return redirect(url_for("project.new"))

    # Create project
    project = Project(title=title, status="created")
    db.session.add(project)
    db.session.flush()

    # Parse and store manuscript sections
    sections = parse_manuscript(manuscript_text)
    _store_sections(project.id, sections)

    # Handle audio file uploads
    audio_files = request.files.getlist("audio_files")
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    stored_files = []

    for idx, f in enumerate(audio_files):
        if f and f.filename and allowed_file(f.filename):
            stored_name, original_name = save_upload(f, upload_folder)
            filepath = os.path.join(upload_folder, stored_name)
            duration = get_duration_ffprobe(filepath)
            sample_rate = get_sample_rate(filepath)

            audio = AudioFile(
                project_id=project.id,
                filename=stored_name,
                original_filename=original_name,
                sort_order=idx,
                duration=duration,
                sample_rate=sample_rate,
            )
            db.session.add(audio)
            stored_files.append(audio)

    db.session.commit()

    # Auto-assign audio files to chapters by order/filename
    _auto_assign_chapters(project.id)
    db.session.commit()

    if stored_files:
        # Go to setup page to review/adjust chapter-audio mapping
        flash(f'Project "{title}" created. Review chapter assignments below.', "success")
        return redirect(url_for("project.setup", project_id=project.id))

    flash(f'Project "{title}" created successfully!', "success")
    return redirect(url_for("dashboard.index"))


@project_bp.route("/<int:project_id>/setup")
def setup(project_id):
    """Chapter-audio assignment page."""
    project = Project.query.get_or_404(project_id)

    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    audio_files = AudioFile.query.filter_by(
        project_id=project_id
    ).order_by(AudioFile.sort_order).all()

    return render_template(
        "project_setup.html",
        project=project,
        chapters=chapters,
        audio_files=audio_files,
    )


@project_bp.route("/<int:project_id>/assign", methods=["POST"])
def assign_chapters(project_id):
    """Save chapter-audio assignments and start processing."""
    project = Project.query.get_or_404(project_id)

    # Save audio mode
    audio_mode = request.form.get("audio_mode", "chapterized")
    if audio_mode in ("chapterized", "continuous"):
        project.audio_mode = audio_mode

    audio_files = AudioFile.query.filter_by(project_id=project_id).all()

    if audio_mode == "chapterized":
        # Read assignments from form: audio_chapter_<audio_id> = chapter_id
        for af in audio_files:
            chapter_val = request.form.get(f"audio_chapter_{af.id}", "")
            if chapter_val and chapter_val.isdigit():
                af.chapter_id = int(chapter_val)
            else:
                af.chapter_id = None
    else:
        # Continuous mode: clear all chapter assignments (audio spans the whole book)
        for af in audio_files:
            af.chapter_id = None

    # Start processing
    project.status = "processing"
    db.session.commit()
    _start_processing(current_app._get_current_object(), project.id)

    flash("Processing started. You'll be redirected to the editor when ready.", "success")
    return redirect(url_for("editor.edit", project_id=project.id))


@project_bp.route("/<int:project_id>/delete", methods=["POST"])
def delete(project_id):
    project = Project.query.get_or_404(project_id)
    upload_folder = current_app.config["UPLOAD_FOLDER"]

    for af in project.audio_files:
        filepath = os.path.join(upload_folder, af.filename)
        if os.path.exists(filepath):
            os.remove(filepath)

    db.session.delete(project)
    db.session.commit()
    flash(f'Project "{project.title}" deleted.', "success")
    return redirect(url_for("dashboard.index"))


@project_bp.route("/<int:project_id>/reprocess", methods=["POST"])
def reprocess(project_id):
    project = Project.query.get_or_404(project_id)
    if project.audio_files:
        AlignmentSegment.query.filter_by(project_id=project_id).delete()
        Conflict.query.filter_by(project_id=project_id).delete()
        Take.query.filter_by(project_id=project_id).delete()
        project.status = "processing"
        db.session.commit()
        _start_processing(current_app._get_current_object(), project_id)
        flash("Reprocessing started.", "info")
    else:
        flash("No audio files to process.", "error")
    return redirect(url_for("editor.edit", project_id=project_id))


def _store_sections(project_id: int, sections: list[dict], parent_id=None):
    """Recursively store manuscript sections."""
    for section in sections:
        ms = ManuscriptSection(
            project_id=project_id,
            section_type=section["section_type"],
            section_index=section["section_index"],
            text_content=section["text_content"],
            parent_id=parent_id,
        )
        db.session.add(ms)
        db.session.flush()

        for child in section.get("children", []):
            child_ms = ManuscriptSection(
                project_id=project_id,
                section_type=child["section_type"],
                section_index=child["section_index"],
                text_content=child["text_content"],
                parent_id=ms.id,
            )
            db.session.add(child_ms)


def _auto_assign_chapters(project_id: int):
    """Try to auto-match audio files to chapters by filename or order.

    Strategies:
    1. Match filenames containing chapter numbers (e.g. "ch01.wav", "chapter_1.mp3")
    2. Fall back to matching by upload order = chapter order
    """
    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    audio_files = AudioFile.query.filter_by(
        project_id=project_id
    ).order_by(AudioFile.sort_order).all()

    if not chapters or not audio_files:
        return

    chapter_map = {}  # chapter_index -> ManuscriptSection
    for ch in chapters:
        chapter_map[ch.section_index] = ch

    assigned = set()
    for af in audio_files:
        num = _extract_number_from_filename(af.original_filename)
        if num is not None and num in chapter_map and num not in assigned:
            af.chapter_id = chapter_map[num].id
            assigned.add(num)

    # Assign remaining by order
    unassigned_audio = [af for af in audio_files if af.chapter_id is None]
    unassigned_chapters = [
        ch for ch in chapters
        if ch.id not in {af.chapter_id for af in audio_files if af.chapter_id}
    ]

    for af, ch in zip(unassigned_audio, unassigned_chapters):
        af.chapter_id = ch.id


def _extract_number_from_filename(filename: str) -> int | None:
    """Extract a chapter/section number from a filename."""
    name = os.path.splitext(filename)[0].lower()
    patterns = [
        r'ch(?:apter)?[_\s.-]*(\d+)',
        r'part[_\s.-]*(\d+)',
        r'section[_\s.-]*(\d+)',
        r'^(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            return int(match.group(1))
    return None


def _start_processing(app, project_id: int):
    """Run alignment processing in a background thread."""
    thread = threading.Thread(target=_process_project, args=(app, project_id))
    thread.daemon = True
    thread.start()


def _process_project(app, project_id: int):
    """Process a project. Dispatches to chapterized or continuous processing."""
    with app.app_context():
        try:
            project = Project.query.get(project_id)
            if not project:
                return

            if project.audio_mode == "continuous":
                _process_continuous(app, project_id)
            else:
                _process_chapterized(app, project_id)

            project = Project.query.get(project_id)
            project.status = "ready"
            db.session.commit()

        except Exception as e:
            project = Project.query.get(project_id)
            if project:
                project.status = "ready"
                db.session.commit()
            print(f"Processing error for project {project_id}: {e}")


def _process_chapterized(app, project_id: int):
    """Process a chapterized project: each chapter's audio is aligned to its text independently."""
    upload_folder = app.config["UPLOAD_FOLDER"]

    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    global_seg_idx = 0

    for chapter in chapters:
        chapter.processing_status = "processing"
        db.session.commit()

        paragraphs = ManuscriptSection.query.filter_by(
            parent_id=chapter.id, section_type="paragraph"
        ).order_by(ManuscriptSection.section_index).all()

        chapter_text = " ".join(p.text_content for p in paragraphs)
        if not chapter_text:
            chapter.processing_status = "ready"
            db.session.commit()
            continue

        chapter_audio = AudioFile.query.filter_by(
            project_id=project_id, chapter_id=chapter.id
        ).order_by(AudioFile.sort_order).all()

        if not chapter_audio:
            chapter.processing_status = "ready"
            db.session.commit()
            continue

        chapter_segments = []

        for audio in chapter_audio:
            filepath = os.path.join(upload_folder, audio.filename)
            if not os.path.exists(filepath):
                continue

            transcript_words = transcribe_audio(
                filepath, manuscript_text=chapter_text
            )
            aligned = align_transcript_to_manuscript(
                transcript_words, chapter_text
            )

            for seg in aligned:
                seg["segment_index"] = global_seg_idx

                db_seg = AlignmentSegment(
                    project_id=project_id,
                    audio_file_id=audio.id,
                    text=seg["text"],
                    expected_text=seg["expected_text"],
                    start_time=seg["start_time"],
                    end_time=seg["end_time"],
                    confidence=seg["confidence"],
                    segment_type=seg["segment_type"],
                    segment_index=global_seg_idx,
                )
                if paragraphs:
                    db_seg.manuscript_section_id = _find_best_section(
                        seg, paragraphs
                    )
                db.session.add(db_seg)
                db.session.flush()

                if seg["start_time"] != seg["end_time"]:
                    take = Take(
                        project_id=project_id,
                        segment_id=db_seg.id,
                        audio_file_id=audio.id,
                        start_time=seg["start_time"],
                        end_time=seg["end_time"],
                        take_number=1,
                        is_selected=True,
                        confidence=seg["confidence"],
                    )
                    db.session.add(take)

                global_seg_idx += 1

            chapter_segments.extend(aligned)

            retakes = detect_retakes(transcript_words, chapter_text)
            for group in retakes:
                for i, take_info in enumerate(group["takes"]):
                    existing = AlignmentSegment.query.filter_by(
                        project_id=project_id,
                        audio_file_id=audio.id,
                        start_time=take_info["start_time"],
                    ).first()
                    if existing:
                        take = Take(
                            project_id=project_id,
                            segment_id=existing.id,
                            audio_file_id=audio.id,
                            start_time=take_info["start_time"],
                            end_time=take_info["end_time"],
                            take_number=i + 1,
                            is_selected=(i == 0),
                            confidence=take_info["confidence"],
                        )
                        db.session.add(take)

        conflicts = detect_conflicts(chapter_segments)
        for c in conflicts:
            matching_seg = AlignmentSegment.query.filter_by(
                project_id=project_id,
                segment_index=c["segment_index"],
            ).first()
            if matching_seg:
                conflict = Conflict(
                    project_id=project_id,
                    segment_id=matching_seg.id,
                    conflict_type=c["conflict_type"],
                    status=c["status"],
                    detected_text=c.get("detected_text", ""),
                    expected_text=c.get("expected_text", ""),
                )
                db.session.add(conflict)

        chapter.processing_status = "ready"
        db.session.commit()

    # Process any unassigned audio against full manuscript text
    _process_unassigned_audio(project_id, upload_folder, global_seg_idx)


def _process_continuous(app, project_id: int):
    """Process continuous audio: transcribe all files as one stream, then align
    against the full manuscript and split results into chapters by text position.

    This handles single-file recordings, arbitrarily-split files, or any audio
    that doesn't align 1:1 with chapters.
    """
    upload_folder = app.config["UPLOAD_FOLDER"]

    chapters = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="chapter"
    ).order_by(ManuscriptSection.section_index).all()

    # Mark all chapters as processing
    for chapter in chapters:
        chapter.processing_status = "processing"
    db.session.commit()

    # Get all audio files in order
    audio_files = AudioFile.query.filter_by(
        project_id=project_id
    ).order_by(AudioFile.sort_order).all()

    if not audio_files:
        for chapter in chapters:
            chapter.processing_status = "ready"
        db.session.commit()
        return

    # Step 1: Transcribe all audio files sequentially, tracking cumulative offset.
    # Each word gets a "global" time (relative to the combined audio stream)
    # plus a reference to which audio file it came from and its local time.
    all_transcript_words = []  # combined transcript
    audio_boundaries = []  # [(audio_file, global_start, global_end), ...]
    cumulative_offset = 0.0

    for audio in audio_files:
        filepath = os.path.join(upload_folder, audio.filename)
        if not os.path.exists(filepath):
            continue

        local_words = transcribe_audio(filepath, manuscript_text=full_text)

        file_duration = audio.duration if audio.duration > 0 else (
            local_words[-1]["end"] if local_words else 0.0
        )

        audio_boundaries.append({
            "audio_file": audio,
            "global_start": cumulative_offset,
            "global_end": cumulative_offset + file_duration,
        })

        # Add words with global timestamps and audio file reference
        for w in local_words:
            all_transcript_words.append({
                "word": w["word"],
                "start": w["start"] + cumulative_offset,
                "end": w["end"] + cumulative_offset,
                "confidence": w["confidence"],
                "local_start": w["start"],
                "local_end": w["end"],
                "audio_file_id": audio.id,
            })

        cumulative_offset += file_duration

    if not all_transcript_words:
        for chapter in chapters:
            chapter.processing_status = "ready"
        db.session.commit()
        return

    # Step 2: Build the full manuscript text and a word-to-paragraph map.
    # We need to know which manuscript word index belongs to which paragraph/chapter.
    all_paragraphs = []
    para_word_ranges = []  # [(para, start_word_idx, end_word_idx), ...]
    full_words = []

    for chapter in chapters:
        paras = ManuscriptSection.query.filter_by(
            parent_id=chapter.id, section_type="paragraph"
        ).order_by(ManuscriptSection.section_index).all()

        for para in paras:
            word_start = len(full_words)
            para_words = para.text_content.split()
            full_words.extend(para_words)
            word_end = len(full_words)
            para_word_ranges.append((para, chapter, word_start, word_end))
            all_paragraphs.append(para)

    full_text = " ".join(p.text_content for p in all_paragraphs)

    if not full_text:
        for chapter in chapters:
            chapter.processing_status = "ready"
        db.session.commit()
        return

    # Step 3: Align the full transcript against the full manuscript
    aligned = align_transcript_to_manuscript(all_transcript_words, full_text)

    # Step 4: Store aligned segments, assigning each to the correct paragraph/chapter
    # and the correct audio file based on its timing.
    global_seg_idx = 0
    # Track which manuscript word we're at based on expected_text sequence
    manuscript_word_cursor = 0

    # Pre-build a lookup: for a given manuscript word index, which paragraph/chapter?
    def _find_para_for_manuscript_word(word_idx):
        for para, chapter, w_start, w_end in para_word_ranges:
            if w_start <= word_idx < w_end:
                return para, chapter
        # Default to last paragraph
        if para_word_ranges:
            return para_word_ranges[-1][0], para_word_ranges[-1][1]
        return None, None

    # Pre-build a lookup: for a given global time, which audio file?
    def _find_audio_for_time(global_time):
        for boundary in audio_boundaries:
            if boundary["global_start"] <= global_time <= boundary["global_end"] + 0.1:
                return boundary["audio_file"]
        # Default to closest
        if audio_boundaries:
            return audio_boundaries[-1]["audio_file"]
        return audio_files[0] if audio_files else None

    # Group aligned segments by chapter for conflict detection
    chapter_aligned = {ch.id: [] for ch in chapters}

    for seg in aligned:
        seg["segment_index"] = global_seg_idx

        # Determine which audio file this segment's time falls within
        audio_file = _find_audio_for_time(seg["start_time"])
        audio_file_id = audio_file.id if audio_file else None

        # Convert global time back to local time for the audio file
        local_start = seg["start_time"]
        local_end = seg["end_time"]
        if audio_file:
            for boundary in audio_boundaries:
                if boundary["audio_file"].id == audio_file_id:
                    local_start = seg["start_time"] - boundary["global_start"]
                    local_end = seg["end_time"] - boundary["global_start"]
                    break

        # Determine which paragraph this segment belongs to
        para, chapter = _find_para_for_manuscript_word(manuscript_word_cursor)
        para_id = para.id if para else None
        chapter_id_for_seg = chapter.id if chapter else None

        # Advance manuscript word cursor for non-extra words
        if seg.get("expected_text") and seg.get("alignment") != "extra":
            manuscript_word_cursor += 1

        db_seg = AlignmentSegment(
            project_id=project_id,
            audio_file_id=audio_file_id,
            manuscript_section_id=para_id,
            text=seg["text"],
            expected_text=seg["expected_text"],
            start_time=local_start,
            end_time=local_end,
            confidence=seg["confidence"],
            segment_type=seg["segment_type"],
            segment_index=global_seg_idx,
        )
        db.session.add(db_seg)
        db.session.flush()

        if local_start != local_end:
            take = Take(
                project_id=project_id,
                segment_id=db_seg.id,
                audio_file_id=audio_file_id or (audio_files[0].id if audio_files else 0),
                start_time=local_start,
                end_time=local_end,
                take_number=1,
                is_selected=True,
                confidence=seg["confidence"],
            )
            db.session.add(take)

        if chapter_id_for_seg:
            chapter_aligned[chapter_id_for_seg].append(seg)

        global_seg_idx += 1

    # Step 5: Detect conflicts per chapter and commit chapter by chapter
    for chapter in chapters:
        ch_segments = chapter_aligned.get(chapter.id, [])
        if ch_segments:
            conflicts = detect_conflicts(ch_segments)
            for c in conflicts:
                matching_seg = AlignmentSegment.query.filter_by(
                    project_id=project_id,
                    segment_index=c["segment_index"],
                ).first()
                if matching_seg:
                    conflict = Conflict(
                        project_id=project_id,
                        segment_id=matching_seg.id,
                        conflict_type=c["conflict_type"],
                        status=c["status"],
                        detected_text=c.get("detected_text", ""),
                        expected_text=c.get("expected_text", ""),
                    )
                    db.session.add(conflict)

        chapter.processing_status = "ready"
        db.session.commit()

    # Detect retakes across the full transcript
    for boundary in audio_boundaries:
        audio = boundary["audio_file"]
        # Get the local words for this audio file
        local_words = [
            {"word": w["word"], "start": w["local_start"], "end": w["local_end"],
             "confidence": w["confidence"]}
            for w in all_transcript_words if w["audio_file_id"] == audio.id
        ]
        if local_words:
            retakes = detect_retakes(local_words, full_text)
            for group in retakes:
                for i, take_info in enumerate(group["takes"]):
                    existing = AlignmentSegment.query.filter_by(
                        project_id=project_id,
                        audio_file_id=audio.id,
                        start_time=take_info["start_time"],
                    ).first()
                    if existing:
                        take = Take(
                            project_id=project_id,
                            segment_id=existing.id,
                            audio_file_id=audio.id,
                            start_time=take_info["start_time"],
                            end_time=take_info["end_time"],
                            take_number=i + 1,
                            is_selected=(i == 0),
                            confidence=take_info["confidence"],
                        )
                        db.session.add(take)
            db.session.commit()


def _process_unassigned_audio(project_id: int, upload_folder: str, global_seg_idx: int):
    """Process any audio files not assigned to a chapter (chapterized mode fallback)."""
    unassigned = AudioFile.query.filter_by(
        project_id=project_id, chapter_id=None
    ).all()

    if not unassigned:
        return

    all_paragraphs = ManuscriptSection.query.filter_by(
        project_id=project_id, section_type="paragraph"
    ).order_by(ManuscriptSection.section_index).all()
    full_text = " ".join(p.text_content for p in all_paragraphs)

    if not full_text:
        return

    for audio in unassigned:
        filepath = os.path.join(upload_folder, audio.filename)
        if not os.path.exists(filepath):
            continue

        transcript_words = transcribe_audio(filepath, manuscript_text=full_text)
        aligned = align_transcript_to_manuscript(transcript_words, full_text)

        chapter_segments = []
        for seg in aligned:
            seg["segment_index"] = global_seg_idx
            db_seg = AlignmentSegment(
                project_id=project_id,
                audio_file_id=audio.id,
                text=seg["text"],
                expected_text=seg["expected_text"],
                start_time=seg["start_time"],
                end_time=seg["end_time"],
                confidence=seg["confidence"],
                segment_type=seg["segment_type"],
                segment_index=global_seg_idx,
            )
            if all_paragraphs:
                db_seg.manuscript_section_id = _find_best_section(
                    seg, all_paragraphs
                )
            db.session.add(db_seg)
            db.session.flush()

            if seg["start_time"] != seg["end_time"]:
                take = Take(
                    project_id=project_id,
                    segment_id=db_seg.id,
                    audio_file_id=audio.id,
                    start_time=seg["start_time"],
                    end_time=seg["end_time"],
                    take_number=1,
                    is_selected=True,
                    confidence=seg["confidence"],
                )
                db.session.add(take)

            global_seg_idx += 1
        chapter_segments.extend(aligned)

        conflicts = detect_conflicts(chapter_segments)
        for c in conflicts:
            matching_seg = AlignmentSegment.query.filter_by(
                project_id=project_id,
                segment_index=c["segment_index"],
            ).first()
            if matching_seg:
                conflict = Conflict(
                    project_id=project_id,
                    segment_id=matching_seg.id,
                    conflict_type=c["conflict_type"],
                    status=c["status"],
                    detected_text=c.get("detected_text", ""),
                    expected_text=c.get("expected_text", ""),
                )
                db.session.add(conflict)
        db.session.commit()


def _find_best_section(seg: dict, paragraphs: list) -> int | None:
    """Find the manuscript section that best matches this segment's expected text."""
    expected = seg.get("expected_text", "").lower()
    if not expected:
        return paragraphs[0].id if paragraphs else None

    for p in paragraphs:
        if expected in p.text_content.lower():
            return p.id

    return paragraphs[0].id if paragraphs else None
