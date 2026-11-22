"""Pick the best moments in a video.

Cheap pass over the whole thing (audio + metadata only), then download and transcribe ONLY the winners.
Signals per second: YouTube "most replayed", Twitch viewer clips, chat rate, loudness jumps.
"""
import time
import numpy as np
from . import sources
from . import transcribe as tr
from .transcribe import refine, spoken_language, transcribe, transcribe_long, wrong_language
from .util import cfg, fmt_t, is_url, job_dir, llm_json, load_json, next_clip_number, save_clip, save_json


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
        big = (meta.get("view_count") or 0) >= 1_000_000        # chat is worth waiting for on a huge stream
        chat = sources.youtube_chat_series(src, d, n, w.get("chat_timeout_big", 900) if big else w.get("chat_timeout", 180))
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


SCAN = """You are finding clip-worthy moments in one hour of a live game stream, from its transcript alone.

Streamer: {who}
Stream title: {title}
Moments people are searching for right now: {topics}

Transcript, one line per sentence, with the time it was said:
{text}

Pick up to {n} moments where something actually HAPPENS and the streamer reacts to it: a win, a fail, a crash, a boss or
mission beaten, an ending, a glitch, a jump scare, a genuinely funny exchange. The cause must be clear from the words.
Skip: menus and settings talk, reading donations or chat, ads, sponsor reads, "be right back", planning what to do next,
and loud talking with nothing behind it. If the hour has nothing, return an empty list.

Reply with ONLY a JSON object:
{{"moments": [{{"at": "h:mm:ss of the payoff itself", "score": 1-10, "why": "what happens, in under 15 words",
               "topic": "one of the searched moments above, copied exactly, or null"}}]}}"""


HUNT = """You are searching one hour of a live game stream's transcript for footage of specific things people are
searching for on YouTube.

Streamer: {who}
Stream title: {title}
Searched topics: {topics}

Transcript, one line per sentence, with the time it was said:
{text}

For each searched topic, is there a moment in this hour that genuinely SHOWS it (the streamer finds the hidden place,
triggers the glitch, gets the car, reaches the ending, discovers the easter egg)? Mentioning the topic in passing does
not count: it has to be happening on screen at that time. Most hours will have none. Up to {n} moments.

Reply with ONLY a JSON object:
{{"moments": [{{"at": "h:mm:ss when it happens", "score": 1-10 for how clearly it shows the topic, "why": "what happens,
               under 15 words", "topic": "the searched topic, copied exactly"}}]}}"""


def _seconds(stamp):
    try:
        parts = [float(x) for x in str(stamp).strip("[] ").split(":")]
    except ValueError:
        return None
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def transcript_moments(audio, d, meta, log=print, focus=None):
    """Read what was said across the whole stream and let the assistant point at the moments.
    Used when a stream has no chat, viewer-clip or most-replayed signal, where loudness alone picks badly."""
    from . import topics
    cache = d / "full_transcript.json"
    segs = load_json(cache)
    if segs is None:
        lang = spoken_language(audio)
        if wrong_language(lang):                 # the video's own language tag is often missing, so listen first
            log(f"  skipped: the speech is {lang}, and this page posts in {', '.join(cfg()['auto']['languages'])}")
            save_json(cache, [])
            return []
        log("transcribing the whole stream (the slow part, once per stream)")
        segs = transcribe_long(audio, log)
        save_json(cache, segs)
    if not segs:
        return []
    hot = "; ".join(focus or [p for p, _ in topics.moments()[:30]]) or "none"
    template = HUNT if focus else SCAN
    picks, hour = [], 3600
    for h in range(0, int(segs[-1][0]) + 1, hour):
        lines = [f"[{fmt_t(s)}] {t}" for s, t in segs if h <= s < h + hour]
        if len(lines) < 20:
            continue
        try:
            j = llm_json(template.format(who=meta.get("uploader") or "", title=meta.get("title") or "", topics=hot,
                                     text="\n".join(lines)[:90000], n=cfg()["scoring"].get("scan_per_hour", 4)), timeout=300)
        except Exception as e:
            log(f"  hour {h // hour + 1}: no answer ({str(e)[:60]})")
            continue
        for m in j.get("moments", []):
            t = _seconds(m.get("at"))
            if t is not None and h - 60 <= t <= h + hour + 60:
                if focus and m.get("topic") not in focus:
                    continue
                picks.append({"t": t, "score": int(m.get("score") or 0), "why": str(m.get("why") or ""), "topic": m.get("topic") or ""})
        log(f"  hour {h // hour + 1}: {len(j.get('moments', []))} moments")
    return picks


def hunt(log=print, per_stream=4):
    """Look through every stream we have already transcribed for searched topics we have no clip for yet.
    Each stream is only asked about a topic once."""
    from . import topics
    from .util import JOBS
    open_topics = [p for p, _ in topics.moments(only_open=True)]
    made = []
    for tf in sorted(JOBS.glob("*/full_transcript.json")):
        d = tf.parent
        meta = load_json(d / "info.json", {})
        url = meta.get("webpage_url")
        asked = load_json(d / "hunted.json", [])
        todo = [t for t in open_topics if t not in asked][:25]
        if not url or not todo:
            continue
        log(f"hunting {len(todo)} topics in: {(meta.get('title') or '')[:60]}")
        picks = sorted(transcript_moments(None, d, meta, log, focus=todo), key=lambda m: -m["score"])
        save_json(d / "hunted.json", asked + todo)
        used = []
        for m in picks:
            if len(used) >= per_stream or m["score"] < cfg()["scoring"].get("scan_min_score", 6):
                break
            if any(abs(m["t"] - u) < cfg()["clip"]["max_seconds"] for u in used):
                continue
            used.append(m["t"])
            try:
                c = make_candidate(url, d, meta, m["t"] + 2, [f"topic hunt ({m['topic']}): {m['why']}"], float(m["score"]), log)
                made.append(c)
            except Exception as e:
                log(f"  could not cut {fmt_t(m['t'])}: {str(e)[:80]}")
    log(f"topic hunt made {len(made)} candidates")
    return made


def find_topic(src, topic, top=2, log=print):
    """Look inside one video for moments that show a specific searched topic (works for short videos too)."""
    c = cfg()
    d = job_dir(src)
    meta = sources.info(src)
    save_json(d / "info.json", meta)
    log(f"{meta.get('title')}  ({meta.get('uploader')})")
    audio = sources.fetch_audio(src, d, c["performance"]["max_source_minutes"], meta.get("duration"))
    picks = sorted(transcript_moments(audio, d, meta, log, focus=[topic]), key=lambda m: -m["score"])
    save_json(d / "hunted.json", load_json(d / "hunted.json", []) + [topic])
    made, used = [], []
    for m in picks:
        if len(made) >= top or m["score"] < c["scoring"].get("scan_min_score", 6):
            break
        if any(abs(m["t"] - u) < c["clip"]["max_seconds"] for u in used):
            continue
        used.append(m["t"])
        made.append(make_candidate(src, d, meta, m["t"] + 2, [f"topic video ({topic}): {m['why']}"], float(m["score"]), log))
    return [m for m in made if m]


def find(src, top=None, log=print):
    c = cfg()
    top = top or c["clip"]["top"]
    d = job_dir(src)
    meta = sources.info(src)
    save_json(d / "info.json", meta)
    log(f"{meta.get('title')}  ({meta.get('uploader') or 'local file'})")
    total, sig, vclips = score(src, d, meta, log)
    S = c["scoring"]
    strong = any(k in sig for k in ("most_replayed", "chat", "viewer_clips"))
    scan = S.get("transcript_scan", "auto")
    made = []
    if len(total) > 20 * 60 and (scan == "always" or (scan == "auto" and not strong)):
        audio = sources.fetch_audio(src, d, c["performance"]["max_source_minutes"], meta.get("duration"))
        picks = sorted(transcript_moments(audio, d, meta, log), key=lambda m: -m["score"])
        used = []
        for m in picks:
            if len(made) >= top or m["score"] < S.get("scan_min_score", 6):
                break
            a, b = max(0, int(m["t"]) - 6), min(len(total), int(m["t"]) + 8)
            p = a + int(np.argmax(total[a:b])) if b > a else int(m["t"])     # snap to the loudest second nearby
            if any(abs(p - u) < c["clip"]["max_seconds"] for u in used):
                continue
            used.append(p)
            made.append(make_candidate(src, d, meta, p, [f"transcript: {m['why']}"], float(m["score"]), log))
        log(f"{len(made)} moments from the transcript")
        if made:
            return [m for m in made if m]
    peaks = pick_peaks(total, top, c["clip"]["max_seconds"], c["clip"]["lead"])
    log(f"{len(peaks)} moments picked; fetching just those sections")
    for p in peaks:
        why = [k.replace("_", " ") for k, v in sig.items() if v[p] > 1.0] or ["overall energy"]
        made.append(make_candidate(src, d, meta, p, why, float(total[p]), log))
    return [m for m in made if m]


def trim_silence(media, a, b, min_len):
    """Cut dead air off both ends of a clip and measure what is left inside it.
    Returns (in, out, longest silent run in seconds, share of the clip that is silent)."""
    db = sources.loudness_db(media)
    quiet = db < cfg()["clip"].get("silence_db", -45)
    n = len(quiet)
    if n == 0:
        return a, b, 0, 0.0
    i, j = int(a), min(n, int(np.ceil(b)))
    while i < j - 1 and quiet[i]:                # leading silence
        i += 1
    while j - 1 > i and quiet[j - 1]:            # trailing silence
        j -= 1
    a2 = max(a, float(i)) if i > int(a) else a
    b2 = min(b, j + 0.4) if j < int(np.ceil(b)) else b
    inside = quiet[int(a2):int(np.ceil(b2))]
    longest = run = 0
    for q in inside:
        run = run + 1 if q else 0
        longest = max(longest, run)
    return round(a2, 2), round(b2, 2), longest, float(inside.mean()) if len(inside) else 0.0


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
    heard = tr.last_language
    save_json(cd / "words.json", words)
    t_in, t_out = refine(words, peak - sec_start, section, c["min_seconds"], c["max_seconds"], c["lead"])
    t_in, t_out, quiet_run, quiet_share = trim_silence(section, t_in, t_out, c["min_seconds"])
    dead = ""
    if quiet_run >= c.get("max_silence_seconds", 3):
        dead = f"goes silent for {quiet_run} seconds in the middle"
    elif quiet_share > 0.2:
        dead = f"{quiet_share:.0%} of it is silent"
    elif t_out - t_in < max(10, c["min_seconds"] - 5):
        dead = f"only {t_out - t_in:.0f} seconds are left once the silence is cut off"
    text = " ".join(x["w"] for x in words if t_in <= x["s"] < t_out)
    if not dead and len(text.split()) >= 12 and wrong_language(heard):
        dead = f"the speech is {heard}, not a language this page posts in"
    clip = {"id": f"{d.name}/{n}", "created": time.time(), "status": "candidate",
            "source": str(src), "source_title": meta.get("title") or "", "creator": meta.get("uploader") or meta.get("channel") or "",
            "at": round(sec_start + t_in, 1), "in": round(t_in, 2), "out": round(t_out, 2), "peak": round(peak - sec_start, 2),
            "score": round(sc, 2), "why": why, "text": text,
            "hook": "", "title": "", "caption": "", "hashtags": cfg()["publish"]["default_hashtags"],
            "layout": cfg()["layout"]["default"], "posts": {}}
    if dead:      # dead air never reaches the judge
        clip["status"] = "rejected"
        clip["judge"] = {"score": 1, "reason": f"Not usable: {dead}.", "safe": True, "music": False}
    save_clip(clip)
    log(f"  #{n} at {fmt_t(clip['at'])} ({t_out - t_in:.0f}s, {', '.join(why)}): " + (f"dropped, {dead}" if dead else text[:90]))
    return clip
