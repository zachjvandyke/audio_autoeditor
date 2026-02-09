from datetime import datetime, timezone
from app import db


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    status = db.Column(
        db.String(50), nullable=False, default="created"
    )  # created, processing, ready, exported
    audio_mode = db.Column(
        db.String(50), nullable=False, default="chapterized"
    )  # chapterized, continuous
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    audio_files = db.relationship(
        "AudioFile", backref="project", lazy=True, cascade="all, delete-orphan"
    )
    manuscript_sections = db.relationship(
        "ManuscriptSection", backref="project", lazy=True, cascade="all, delete-orphan"
    )
    segments = db.relationship(
        "AlignmentSegment", backref="project", lazy=True, cascade="all, delete-orphan"
    )
    conflicts = db.relationship(
        "Conflict", backref="project", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def conflict_stats(self):
        total = len(self.conflicts)
        resolved = sum(1 for c in self.conflicts if c.status != "pending")
        return {"total": total, "resolved": resolved, "pending": total - resolved}


class AudioFile(db.Model):
    __tablename__ = "audio_files"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    chapter_id = db.Column(
        db.Integer, db.ForeignKey("manuscript_sections.id"), nullable=True
    )  # which chapter this audio belongs to
    filename = db.Column(db.String(255), nullable=False)  # stored filename
    original_filename = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    duration = db.Column(db.Float, default=0.0)
    sample_rate = db.Column(db.Integer, default=44100)
    uploaded_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    chapter = db.relationship(
        "ManuscriptSection", foreign_keys=[chapter_id], backref="audio_files"
    )
    takes = db.relationship(
        "Take", backref="audio_file", lazy=True, cascade="all, delete-orphan"
    )
    segments = db.relationship(
        "AlignmentSegment", backref="audio_file", lazy=True, cascade="all, delete-orphan"
    )


class ManuscriptSection(db.Model):
    __tablename__ = "manuscript_sections"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    section_type = db.Column(
        db.String(50), nullable=False, default="paragraph"
    )  # chapter, paragraph
    section_index = db.Column(db.Integer, nullable=False, default=0)
    text_content = db.Column(db.Text, nullable=False)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("manuscript_sections.id"), nullable=True
    )
    processing_status = db.Column(
        db.String(50), nullable=False, default="pending"
    )  # pending, processing, ready (for chapters)

    children = db.relationship(
        "ManuscriptSection", backref=db.backref("parent", remote_side="ManuscriptSection.id"),
        lazy=True,
    )
    segments = db.relationship(
        "AlignmentSegment",
        backref="manuscript_section",
        lazy=True,
        cascade="all, delete-orphan",
    )


class AlignmentSegment(db.Model):
    """Word-level alignment between manuscript text and audio."""

    __tablename__ = "alignment_segments"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    audio_file_id = db.Column(
        db.Integer, db.ForeignKey("audio_files.id"), nullable=True
    )
    manuscript_section_id = db.Column(
        db.Integer, db.ForeignKey("manuscript_sections.id"), nullable=True
    )
    text = db.Column(db.String(500), nullable=False)
    expected_text = db.Column(db.String(500), nullable=True)  # from manuscript
    start_time = db.Column(db.Float, nullable=False, default=0.0)
    end_time = db.Column(db.Float, nullable=False, default=0.0)
    confidence = db.Column(db.Float, default=1.0)
    segment_type = db.Column(
        db.String(50), default="word"
    )  # word, phrase, sentence, paragraph, silence
    segment_index = db.Column(db.Integer, default=0)

    takes = db.relationship(
        "Take", backref="segment", lazy=True, cascade="all, delete-orphan"
    )
    conflicts = db.relationship(
        "Conflict", backref="segment", lazy=True, cascade="all, delete-orphan"
    )


class Take(db.Model):
    """Represents one take/recording of a segment (for multi-take selection)."""

    __tablename__ = "takes"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    segment_id = db.Column(
        db.Integer, db.ForeignKey("alignment_segments.id"), nullable=False
    )
    audio_file_id = db.Column(
        db.Integer, db.ForeignKey("audio_files.id"), nullable=False
    )
    start_time = db.Column(db.Float, nullable=False)
    end_time = db.Column(db.Float, nullable=False)
    take_number = db.Column(db.Integer, default=1)
    is_selected = db.Column(db.Boolean, default=False)
    confidence = db.Column(db.Float, default=1.0)


class Conflict(db.Model):
    """Detected discrepancy between manuscript and audio."""

    __tablename__ = "conflicts"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    segment_id = db.Column(
        db.Integer, db.ForeignKey("alignment_segments.id"), nullable=False
    )
    conflict_type = db.Column(
        db.String(50), nullable=False
    )  # misread, pause, noise, missing_word, extra_word, low_confidence
    status = db.Column(
        db.String(50), nullable=False, default="pending"
    )  # pending, ok, needs_edit
    detected_text = db.Column(db.String(500), nullable=True)
    expected_text = db.Column(db.String(500), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
