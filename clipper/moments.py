"""Pick the best moments in a video.

Cheap pass over the whole thing (audio + metadata only), then download and transcribe ONLY the winners.
Signals per second: YouTube "most replayed", Twitch viewer clips, chat rate, loudness jumps.
"""
import time
import numpy as np
from . import sources
from .transcribe import refine, transcribe
from .util import cfg, fmt_t, is_url, job_dir, next_clip_number, save_clip, save_json


def z(a):
    a = np.asarray(a, dtype=float)
    return np.zeros_like(a) if a.std() < 1e-9 else np.clip((a - np.median(a)) / a.std(), 0, None)


def smooth(a, k):
    k = max(1, int(k))
    return np.convolve(a, np.ones(k) / k, mode="same")


def shift_earlier(a, s):
    return np.concatenate([a[s:], np.zeros(min(s, len(a)))]) if s > 0 else a


def score(src, d, meta, log=print):
    c = cfg()
    w = c["scoring"]
    log("downloading audio only and measuring loudness")
    audio = sources.fetch_audio(src, d, c["performance"]["max_source_minutes"], meta.get("duration"))
    db = sources.loudness_db(audio)
    n = len(db)
    sig = {"loudness": smooth(z(db - smooth(db, 30)), 3)}
    heat = np.zeros(n)
    for h in meta.get("heatmap") or []:
        heat[int(h["start_time"]):int(np.ceil(h["end_time"]))] = h["value"]
    if heat.any():
        sig["most_replayed"] = z(heat)
    key = meta.get("extractor_key") or ""
    clips = []
    if key.startswith("Twitch"):
        cs, clips = sources.twitch_clip_series(meta, n)
        if cs.any():
            sig["viewer_clips"] = z(smooth(cs, 5))
    elif key == "Youtube" and meta.get("was_live"):
        log("reading chat replay")
        chat = sources.youtube_chat_series(src, d, n)
        if chat.any():
            sig["chat"] = smooth(z(shift_earlier(smooth(chat, 10), int(w["chat_lag"]))), 3)
    total = sum(w.get(k, 1.0) * v for k, v in sig.items())
    log("signals used: " + ", ".join(sig))
    return total, sig, clips


def pick_peaks(total, top, max_len, lead):
    """Highest-scoring seconds whose windows do not overlap."""
    n, used, out = len(total), np.zeros(len(total), bool), []
    for p in np.argsort(-total):
        if len(out) >= top or total[p] <= 0:
            break
        a, b = max(0, int(p - lead)), min(n, int(p - lead + max_len))
        if used[max(0, a - 5):b + 5].any():
            continue
        used[a:b] = True
        out.append(int(p))
    return sorted(out)


def find(src, top=None, log=print):
    c = cfg()
    top = top or c["clip"]["top"]
    d = job_dir(src)
    meta = sources.info(src)
    save_json(d / "info.json", meta)
    log(f"{meta.get('title')}  ({meta.get('uploader') or 'local file'})")
    total, sig, vclips = score(src, d, meta, log)
    peaks = pick_peaks(total, top, c["clip"]["max_seconds"], c["clip"]["lead"])
    log(f"{len(peaks)} moments picked; fetching just those sections")
    made = []
    for p in peaks:
        why = [k.replace("_", " ") for k, v in sig.items() if v[p] > 1.0] or ["overall energy"]
        made.append(make_candidate(src, d, meta, p, why, float(total[p]), log))
    return [m for m in made if m]


def make_candidate(src, d, meta, peak, why, sc, log=print, section=None, sec_start=None):
    """Fetch the section around a peak, transcribe it on the GPU, snap the cut to sentence edges."""
    c = cfg()["clip"]
    pad = 8
    n = int(section.parent.name) if section is not None else next_clip_number(d)
    cd = d / "clips" / str(n)
    if section is None:
        sec_start = max(0.0, peak - c["lead"] - pad)
        section = sources.fetch_section(src, sec_start, peak - c["lead"] + c["max_seconds"] + pad, cd / "src.mp4")
    words = transcribe(section)
    save_json(cd / "words.json", words)
    t_in, t_out = refine(words, peak - sec_start, section, c["min_seconds"], c["max_seconds"], c["lead"])
    text = " ".join(x["w"] for x in words if t_in <= x["s"] < t_out)
    clip = {"id": f"{d.name}/{n}", "created": time.time(), "status": "candidate",
            "source": str(src), "source_title": meta.get("title") or "", "creator": meta.get("uploader") or meta.get("channel") or "",
            "at": round(sec_start + t_in, 1), "in": round(t_in, 2), "out": round(t_out, 2), "peak": round(peak - sec_start, 2),
            "score": round(sc, 2), "why": why, "text": text,
            "hook": "", "title": "", "caption": "", "hashtags": cfg()["publish"]["default_hashtags"],
            "layout": cfg()["layout"]["default"], "posts": {}}
    save_clip(clip)
    log(f"  #{n} at {fmt_t(clip['at'])} ({t_out - t_in:.0f}s, {', '.join(why)}): {text[:90]}")
    return clip
