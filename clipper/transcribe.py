"""Speech to word timestamps on the Apple GPU (MLX), and snapping clip edges to natural pauses.

Only the short sections that were picked are transcribed, never the whole stream.
"""
import re
from .util import cfg, duration


def transcribe(media):
    """Word timestamps. Apple Silicon: MLX on the GPU. Elsewhere: faster-whisper on an NVIDIA GPU or the CPU."""
    P = cfg()["performance"]
    try:
        import mlx_whisper
    except ImportError:
        return _faster_whisper(media, P.get("whisper_model_other", "small"))
    r = mlx_whisper.transcribe(str(media), path_or_hf_repo=P["whisper_model"],
                               word_timestamps=True, condition_on_previous_text=False, verbose=None)
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


def refine(words, peak, media, min_len, max_len, lead):
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
    t_out = (min(ok, key=lambda e: abs(e - (peak + 8))) + 0.35) if ok else min(hi, max(lo, peak + 8))
    return round(t_in, 2), round(min(total, t_out), 2)
