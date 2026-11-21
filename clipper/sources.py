"""Getting things from YouTube / Twitch: metadata, audio, chat replay, viewer clips, video sections."""
import json, re, subprocess, urllib.request
from pathlib import Path
import numpy as np
from .util import cfg, is_url, run, ytdlp

TWITCH_GQL = "https://gql.twitch.tv/gql"
TWITCH_WEB_CLIENT = "kimne78kx3ncx6brgo4mv6wki5h1ko"   # the public id twitch.tv's own web player uses


def info(src):
    if not is_url(src):
        return {"title": Path(src).stem, "webpage_url": str(src), "uploader": ""}
    j = json.loads(ytdlp("-J", src, capture=True).stdout)
    keep = ("id", "title", "uploader", "uploader_id", "channel", "webpage_url", "duration", "view_count",
            "heatmap", "is_live", "was_live", "extractor_key", "timestamp", "language")
    return {k: j.get(k) for k in keep}


def fetch_audio(src, d, max_minutes, dur=None):
    """Audio only: a few MB per hour instead of gigabytes of video. Enough to score every second."""
    if not is_url(src):
        return Path(src)
    hit = [p for p in d.glob("audio.*") if p.suffix not in (".part", ".ytdl")]
    if hit:
        return hit[0]
    # cutting a section goes through ffmpeg, which YouTube throttles to about real time: only do it when needed
    cut = ["--download-sections", f"*0-{int(max_minutes * 60)}"] if not dur or dur > max_minutes * 60 else []
    ytdlp("-f", "wa/ba/w", *cut, "-o", str(d / "audio.%(ext)s"), src)   # smallest audio: only loudness is measured from it
    return [p for p in d.glob("audio.*") if p.suffix not in (".part", ".ytdl")][0]


def loudness_db(media):
    """RMS level in dB for every second, streamed through ffmpeg so memory stays flat on long VODs."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         stdout=subprocess.PIPE)
    out, sec = [], 8000 * 2
    while True:
        b = p.stdout.read(sec * 60)
        if not b:
            break
        x = np.frombuffer(b[: len(b) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
        for i in range(0, len(x) - 7999, 8000):
            out.append(20 * np.log10(np.sqrt((x[i:i + 8000] ** 2).mean() + 1e-9)))
    p.wait()
    return np.array(out)


CHAT_HYPE = re.compile(r"(?i)LMAO|LOL|KEKW|OMEGALUL|CLIP|NO WAY|HOLY|WTF|\bW+\b|[💀😂🔥]")


def youtube_chat_series(src, d, n, timeout=180):
    """Messages per second from a YouTube live-chat replay (only exists for finished live streams)."""
    c = np.zeros(n)
    if not list(d.glob("chat*.live_chat.json")):
        try:   # chat replay of a huge stream can take longer than everything else combined, so it gets a time limit
            ytdlp("--skip-download", "--write-subs", "--sub-langs", "live_chat", "-o", str(d / "chat"), src, check=False, capture=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            for f in d.glob("chat*"):
                f.unlink()   # a partial replay only covers the start and would bias the picks
            return c
    for f in d.glob("chat*.live_chat.json"):
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                a = json.loads(line)["replayChatItemAction"]
                t = int(a["videoOffsetTimeMsec"]) // 1000
            except Exception:
                continue
            if 0 <= t < n:
                c[t] += 2 if CHAT_HYPE.search(line) else 1
    return c


def twitch_gql(query, variables):
    req = urllib.request.Request(TWITCH_GQL, data=json.dumps({"query": query, "variables": variables}).encode(),
                                 headers={"Client-ID": TWITCH_WEB_CLIENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def twitch_clips(login, period="LAST_DAY", first=50):
    """Clips viewers made on a channel, most viewed first. The closest thing to a live 'most viewed' signal."""
    q = """query($login:String!,$first:Int!,$period:ClipsPeriod!){user(login:$login){clips(first:$first,
           criteria:{period:$period,sort:VIEWS_DESC}){edges{node{slug title viewCount durationSeconds
           videoOffsetSeconds createdAt url video{id}}}}}}"""
    j = twitch_gql(q, {"login": login.lower(), "first": first, "period": period})
    user = (j.get("data") or {}).get("user") or {}
    return [e["node"] for e in ((user.get("clips") or {}).get("edges") or []) if e.get("node")]


def twitch_game_clips(game, languages=("EN",), first=40):
    """Today's most-viewed viewer clips for a game across all of Twitch."""
    langs = "[" + ",".join(languages) + "]" if languages else "[]"
    q = """query($g:String!,$first:Int!){game(name:$g){clips(first:$first,criteria:{period:LAST_DAY,languages:%s}){edges{node{
           slug title viewCount durationSeconds url broadcaster{login displayName}}}}}}""" % langs
    game = ((twitch_gql(q, {"g": game, "first": first}).get("data") or {}).get("game")) or {}
    return [e["node"] for e in ((game.get("clips") or {}).get("edges") or []) if e.get("node") and e["node"].get("broadcaster")]


def twitch_clip_series(meta, n):
    """Per-second weight from viewer clips that point into this Twitch VOD."""
    c = np.zeros(n)
    vod = re.sub(r"\D", "", str(meta.get("id") or ""))
    login = meta.get("uploader_id") or ""
    if not vod or not login:
        return c, []
    try:
        clips = twitch_clips(login, "LAST_WEEK", 100)
    except Exception:
        return c, []
    used = []
    for k in clips:
        off = k.get("videoOffsetSeconds")
        if (k.get("video") or {}).get("id") != vod or off is None:
            continue
        a, b = int(off), int(off + (k.get("durationSeconds") or 30))
        if 0 <= a < n:
            c[a:min(b, n)] += np.log1p(k.get("viewCount") or 0)
            used.append(k)
    return c, used


def fetch_section(src, start, end, out):
    """Only the seconds we need, at full quality. No re-encode here, so it costs almost no CPU."""
    out = Path(out)
    if out.exists() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    if is_url(src):
        h = cfg()["live"].get("height", 1080)
        ytdlp("-f", f"bv*[height<={h}]+ba/b[height<={h}]/b", "--merge-output-format", "mp4",
              "--download-sections", f"*{start:.0f}-{end:.0f}", "-o", str(out), src)
    else:
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", src,
             "-c", "copy", "-avoid_negative_ts", "make_zero", out])
    return out
