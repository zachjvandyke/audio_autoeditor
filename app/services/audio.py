"""Audio processing service - handles file analysis and duration extraction."""
import json
import os
import subprocess
import uuid

from werkzeug.utils import secure_filename


ALLOWED_EXTENSIONS = {"wav", "mp3", "flac", "ogg", "m4a", "aac", "wma"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_upload(file, upload_folder: str) -> tuple[str, str]:
    """Save an uploaded file with a unique name. Returns (stored_filename, original_filename)."""
    original = secure_filename(file.filename)
    ext = original.rsplit(".", 1)[1].lower() if "." in original else "wav"
    stored = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(upload_folder, stored)
    file.save(filepath)
    return stored, original


def get_duration_ffprobe(filepath: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except (subprocess.TimeoutExpired, KeyError, json.JSONDecodeError, FileNotFoundError):
        return 0.0


def get_sample_rate(filepath: str) -> int:
    """Get audio sample rate using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "a:0",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        info = json.loads(result.stdout)
        return int(info["streams"][0]["sample_rate"])
    except (subprocess.TimeoutExpired, KeyError, json.JSONDecodeError, IndexError, FileNotFoundError):
        return 44100


def convert_to_wav(input_path: str, output_path: str, sample_rate: int = 16000) -> bool:
    """Convert audio file to WAV format for processing."""
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-i", input_path,
                "-ar", str(sample_rate),
                "-ac", "1",
                "-y",
                output_path,
            ],
            capture_output=True,
            timeout=120,
        )
        return os.path.exists(output_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
