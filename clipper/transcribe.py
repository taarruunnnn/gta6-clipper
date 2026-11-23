"""Speech to word timestamps on the Apple GPU (MLX), and snapping clip edges to natural pauses.

Only the short sections that were picked are transcribed, never the whole stream.
"""
import re, tempfile
from pathlib import Path
from .util import cfg, duration, run


last_language = ""   # language Whisper heard in the most recent transcribe() call ("en", "pt", ...)


def spoken_language(media, samples=3):
    """Which language is spoken, from a few 30-second samples spread across the file. Takes seconds, and saves
    transcribing hours of a stream the page would never post. Returns "" when it cannot tell."""
    try:
        import mlx_whisper
    except ImportError:
        return ""
    P, total, votes = cfg()["performance"], duration(media), []
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(samples):
            f = f"{tmp}/s{i}.flac"
            run(["ffmpeg", "-v", "error", "-ss", f"{total * (i + 1) / (samples + 1):.0f}", "-t", "30", "-i", media,
                 "-vn", "-ac", "1", "-ar", "16000", f], check=False)
            try:
                r = mlx_whisper.transcribe(f, path_or_hf_repo=P["whisper_model"], condition_on_previous_text=False, verbose=None)
                if len(r.get("text", "").split()) >= 8:        # near-silent samples say nothing about the language
                    votes.append(r.get("language") or "")
            except Exception:
                pass
    return max(set(votes), key=votes.count).upper() if votes else ""


def wrong_language(lang):
    """True when a detected language is known and is not one the page posts in."""
    allowed = [a.upper() for a in cfg()["auto"].get("languages", [])]
    return bool(allowed and lang and lang.upper() not in allowed)


def transcribe(media):
    """Word timestamps. Apple Silicon: MLX on the GPU. Elsewhere: faster-whisper on an NVIDIA GPU or the CPU."""
    P = cfg()["performance"]
    try:
        import mlx_whisper
    except ImportError:
        return _faster_whisper(media, P.get("whisper_model_other", "small"))
    r = mlx_whisper.transcribe(str(media), path_or_hf_repo=P["whisper_model"],
                               word_timestamps=True, condition_on_previous_text=False, verbose=None)
    global last_language
    last_language = (r.get("language") or "").upper()
    words = []
    for seg in r.get("segments", []):
        for w in seg.get("words", []):
            t = w["word"].strip()
            if t:
                words.append({"w": t, "s": round(float(w["start"]), 2), "e": round(float(w["end"]), 2)})
    return words


def _faster_whisper(media, size):
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        gpu = False
    model = WhisperModel(size, device="cuda" if gpu else "cpu", compute_type="float16" if gpu else "int8")
    segs, _ = model.transcribe(str(media), word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    return [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)} for s in segs for w in (s.words or []) if w.word.strip()]


def transcribe_long(media, log=print, chunk=1200):
    """What was said across a whole stream, as [(start_seconds, text)] sentences.
    The audio is cut into 20-minute pieces so memory stays flat however long the stream is."""
    P = cfg()["performance"]
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        run(["ffmpeg", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", "16000", "-f", "segment", "-segment_time", chunk,
             "-c:a", "flac", f"{tmp}/c%04d.flac"], heavy=True)
        files = sorted(Path(tmp).glob("c*.flac"))
        for i, f in enumerate(files):
            try:
                import mlx_whisper
                r = mlx_whisper.transcribe(str(f), path_or_hf_repo=P["whisper_model"], condition_on_previous_text=False, verbose=None)
                segs = [(s["start"], s["text"]) for s in r.get("segments", [])]
            except ImportError:
                segs = _faster_segments(f, P.get("whisper_model_other", "small"))
            out += [(round(i * chunk + s, 1), t.strip()) for s, t in segs if t.strip()]
            if (i + 1) % 3 == 0 or i + 1 == len(files):
                log(f"  transcribed {min((i + 1) * chunk, 10**9) // 60} of about {len(files) * chunk // 60} minutes")
    return out


def _faster_segments(media, size):
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        gpu = False
    model = WhisperModel(size, device="cuda" if gpu else "cpu", compute_type="float16" if gpu else "int8")
    segs, _ = model.transcribe(str(media), vad_filter=True, condition_on_previous_text=False)
    return [(s.start, s.text) for s in segs]


def refine(words, peak, media, min_len, max_len, lead, tail=8):
    """Start on the first word of a sentence, end on a finished one, keep the peak inside."""
    total = duration(media)
    want_in = max(0.0, peak - lead)
    if not words:
        return want_in, min(total, want_in + max_len)
    starts, ends = [], []
    for i, w in enumerate(words):
        prev = words[i - 1] if i else None
        if prev is None or w["s"] - prev["e"] > 0.35 or re.search(r"[.!?]$", prev["w"]):
            starts.append(w["s"])
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or nxt["s"] - w["e"] > 0.35 or re.search(r"[.!?]$", w["w"]):
            ends.append(w["e"])
    ok = [s for s in starts if s <= peak - 2] or [want_in]
    t_in = max(0.0, min(ok, key=lambda s: abs(s - want_in)) - 0.15)
    lo, hi = max(t_in + min_len, peak + 3), t_in + max_len
    ok = [e for e in ends if lo <= e <= hi]
    t_out = (min(ok, key=lambda e: abs(e - (peak + tail))) + 0.35) if ok else min(hi, max(lo, peak + tail))
    return round(t_in, 2), round(min(total, t_out), 2)
