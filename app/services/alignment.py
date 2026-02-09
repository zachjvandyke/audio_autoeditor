"""Text-audio alignment engine.

Uses Whisper for transcription with word-level timestamps, then aligns
the transcription against the manuscript text using sequence matching.
Falls back to simulated alignment when Whisper is not available.
"""
import difflib
import os
import re

from app.services.audio import convert_to_wav


def transcribe_audio(audio_path: str, model_size: str = "base") -> list[dict]:
    """Transcribe audio file and return word-level timestamps.

    Returns list of dicts: [{"word": str, "start": float, "end": float, "confidence": float}, ...]
    """
    # Try faster-whisper first, then openai whisper, then fallback
    words = _try_faster_whisper(audio_path, model_size)
    if words is not None:
        return words

    words = _try_openai_whisper(audio_path, model_size)
    if words is not None:
        return words

    return _simulated_transcription(audio_path)


def _try_faster_whisper(audio_path: str, model_size: str) -> list[dict] | None:
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, word_timestamps=True)

        words = []
        for segment in segments:
            if segment.words:
                for w in segment.words:
                    words.append({
                        "word": w.word.strip(),
                        "start": w.start,
                        "end": w.end,
                        "confidence": w.probability,
                    })
        return words if words else None
    except ImportError:
        return None
    except Exception:
        return None


def _try_openai_whisper(audio_path: str, model_size: str) -> list[dict] | None:
    try:
        import whisper

        model = whisper.load_model(model_size)
        result = model.transcribe(audio_path, word_timestamps=True)

        words = []
        for segment in result.get("segments", []):
            for w in segment.get("words", []):
                words.append({
                    "word": w["word"].strip(),
                    "start": w["start"],
                    "end": w["end"],
                    "confidence": w.get("probability", 0.9),
                })
        return words if words else None
    except ImportError:
        return None
    except Exception:
        return None


def _simulated_transcription(audio_path: str) -> list[dict]:
    """Fallback: generate simulated word timings for development/testing.

    Creates plausible word timings based on audio duration.
    """
    from app.services.audio import get_duration_ffprobe

    duration = get_duration_ffprobe(audio_path)
    if duration <= 0:
        duration = 60.0  # default for testing

    # Generate placeholder words at ~2.5 words/second (typical speech rate)
    words_per_second = 2.5
    num_words = max(1, int(duration * words_per_second))
    word_duration = duration / num_words

    words = []
    for i in range(num_words):
        start = i * word_duration
        end = start + word_duration * 0.85  # small gap between words
        words.append({
            "word": f"word_{i}",
            "start": round(start, 3),
            "end": round(end, 3),
            "confidence": 0.5,  # low confidence signals simulated data
        })
    return words


def align_transcript_to_manuscript(
    transcript_words: list[dict], manuscript_text: str
) -> list[dict]:
    """Align transcribed words to manuscript text using sequence matching.

    Returns aligned segments with both detected and expected text, plus timing.
    """
    # Normalize manuscript words
    manuscript_words = _normalize_text_to_words(manuscript_text)
    transcript_texts = [_normalize_word(w["word"]) for w in transcript_words]

    # Use SequenceMatcher for alignment
    matcher = difflib.SequenceMatcher(
        None, transcript_texts, manuscript_words, autojunk=False
    )
    opcodes = matcher.get_opcodes()

    aligned = []
    seg_idx = 0

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            # Words match between transcript and manuscript
            for ti, mi in zip(range(i1, i2), range(j1, j2)):
                tw = transcript_words[ti]
                aligned.append({
                    "text": tw["word"],
                    "expected_text": manuscript_words[mi],
                    "start_time": tw["start"],
                    "end_time": tw["end"],
                    "confidence": tw["confidence"],
                    "segment_type": "word",
                    "segment_index": seg_idx,
                    "alignment": "match",
                })
                seg_idx += 1

        elif tag == "replace":
            # Mismatched words - possible misreads
            t_range = list(range(i1, i2))
            m_range = list(range(j1, j2))
            max_len = max(len(t_range), len(m_range))

            for k in range(max_len):
                if k < len(t_range) and k < len(m_range):
                    tw = transcript_words[t_range[k]]
                    aligned.append({
                        "text": tw["word"],
                        "expected_text": manuscript_words[m_range[k]],
                        "start_time": tw["start"],
                        "end_time": tw["end"],
                        "confidence": tw["confidence"],
                        "segment_type": "word",
                        "segment_index": seg_idx,
                        "alignment": "mismatch",
                    })
                elif k < len(t_range):
                    # Extra word in audio (not in manuscript)
                    tw = transcript_words[t_range[k]]
                    aligned.append({
                        "text": tw["word"],
                        "expected_text": "",
                        "start_time": tw["start"],
                        "end_time": tw["end"],
                        "confidence": tw["confidence"],
                        "segment_type": "word",
                        "segment_index": seg_idx,
                        "alignment": "extra",
                    })
                else:
                    # Missing word - in manuscript but not in audio
                    # Estimate timing from surrounding words
                    est_time = _estimate_missing_time(transcript_words, i1, i2)
                    aligned.append({
                        "text": "",
                        "expected_text": manuscript_words[m_range[k]],
                        "start_time": est_time,
                        "end_time": est_time,
                        "confidence": 0.0,
                        "segment_type": "word",
                        "segment_index": seg_idx,
                        "alignment": "missing",
                    })
                seg_idx += 1

        elif tag == "insert":
            # Words in manuscript but not in audio (missing from recording)
            est_time = _estimate_missing_time(transcript_words, i1, i1)
            for mi in range(j1, j2):
                aligned.append({
                    "text": "",
                    "expected_text": manuscript_words[mi],
                    "start_time": est_time,
                    "end_time": est_time,
                    "confidence": 0.0,
                    "segment_type": "word",
                    "segment_index": seg_idx,
                    "alignment": "missing",
                })
                seg_idx += 1

        elif tag == "delete":
            # Words in audio but not in manuscript (extra words)
            for ti in range(i1, i2):
                tw = transcript_words[ti]
                aligned.append({
                    "text": tw["word"],
                    "expected_text": "",
                    "start_time": tw["start"],
                    "end_time": tw["end"],
                    "confidence": tw["confidence"],
                    "segment_type": "word",
                    "segment_index": seg_idx,
                    "alignment": "extra",
                })
                seg_idx += 1

    return aligned


def detect_retakes(transcript_words: list[dict], manuscript_text: str) -> list[dict]:
    """Detect multiple takes of the same passage in the audio.

    Looks for repeated sequences of words that match the same manuscript section.
    Returns groups of takes with their timing info.
    """
    manuscript_words = _normalize_text_to_words(manuscript_text)
    transcript_texts = [_normalize_word(w["word"]) for w in transcript_words]

    retake_groups = []
    # Sliding window to find repeated manuscript passages
    min_phrase_len = 3  # minimum words to consider a phrase

    for phrase_len in range(min_phrase_len, min(20, len(manuscript_words) + 1)):
        for m_start in range(len(manuscript_words) - phrase_len + 1):
            phrase = manuscript_words[m_start : m_start + phrase_len]
            occurrences = _find_phrase_occurrences(transcript_texts, phrase)

            if len(occurrences) > 1:
                takes = []
                for occ_start in occurrences:
                    occ_end = occ_start + phrase_len - 1
                    takes.append({
                        "start_idx": occ_start,
                        "end_idx": occ_end,
                        "start_time": transcript_words[occ_start]["start"],
                        "end_time": transcript_words[occ_end]["end"],
                        "confidence": sum(
                            transcript_words[i]["confidence"]
                            for i in range(occ_start, occ_end + 1)
                        ) / phrase_len,
                        "text": " ".join(
                            transcript_words[i]["word"]
                            for i in range(occ_start, occ_end + 1)
                        ),
                    })

                retake_groups.append({
                    "manuscript_start": m_start,
                    "manuscript_end": m_start + phrase_len,
                    "expected_text": " ".join(phrase),
                    "takes": takes,
                })

    # Deduplicate: keep the longest phrases
    retake_groups = _deduplicate_retake_groups(retake_groups)
    return retake_groups


def _normalize_word(word: str) -> str:
    """Normalize a word for comparison."""
    return re.sub(r"[^\w']", "", word.lower().strip())


def _normalize_text_to_words(text: str) -> list[str]:
    """Extract and normalize words from text."""
    cleaned = re.sub(r"[^\w\s']", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return [_normalize_word(w) for w in cleaned.split() if _normalize_word(w)]


def _estimate_missing_time(
    transcript_words: list[dict], i1: int, i2: int
) -> float:
    """Estimate the timestamp for a missing word based on surrounding context."""
    if i2 < len(transcript_words):
        return transcript_words[i2]["start"]
    elif i1 > 0:
        return transcript_words[i1 - 1]["end"]
    return 0.0


def _find_phrase_occurrences(
    words: list[str], phrase: list[str]
) -> list[int]:
    """Find all starting indices where phrase occurs in words."""
    occurrences = []
    phrase_len = len(phrase)
    for i in range(len(words) - phrase_len + 1):
        if words[i : i + phrase_len] == phrase:
            occurrences.append(i)
    return occurrences


def _deduplicate_retake_groups(groups: list[dict]) -> list[dict]:
    """Keep only the longest non-overlapping retake groups."""
    if not groups:
        return []

    # Sort by phrase length (longest first)
    groups.sort(key=lambda g: g["manuscript_end"] - g["manuscript_start"], reverse=True)

    kept = []
    covered_ranges = set()

    for group in groups:
        m_range = set(range(group["manuscript_start"], group["manuscript_end"]))
        if not m_range & covered_ranges:
            kept.append(group)
            covered_ranges |= m_range

    return sorted(kept, key=lambda g: g["manuscript_start"])
