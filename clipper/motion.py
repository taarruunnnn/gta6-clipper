"""Motion style (and the reacting avatar): animated hook, popping captions, on-screen commentary beats, impact flash, sound effects.

Remotion renders ONLY the transparent text layer (cheap: no video decoding in the browser); ffmpeg keeps doing
the footage with hardware decode/encode. Switched on with `motion = true` under [brand] in config.toml.
"""
import shutil, subprocess
import numpy as np
from . import overlay
from .util import ROOT, cfg, run, save_json

MOTION = ROOT / "motion"
SFX = ROOT / "brand" / "sfx"


BANNED_EMOJI = {"🔪", "🗡️", "🗡", "🔫", "💣", "🩸", "⚔️", "⚔", "🪓"}   # nothing violent pops up on screen


def available():
    return (MOTION / "node_modules" / ".bin" / "remotion").exists() and shutil.which("node")


def beats(clip, length, vo):
    """Our commentary as text: a 'watch' cue just before the peak and a reaction right after it."""
    pk = max(0.5, clip["peak"] - clip["in"])
    out = []
    if clip.get("peak_text") and pk - 2.2 > vo + 0.3:
        out.append({"kind": "watch", "text": clip["peak_text"], "start": round(pk - 2.2, 2), "end": round(pk + 0.1, 2)})
    if clip.get("payoff_text") and pk + 0.9 < length - 1.2:
        out.append({"kind": "payoff", "text": clip["payoff_text"], "start": round(pk + 0.9, 2), "end": round(min(length - 0.2, pk + 3.4), 2)})
    ask = max(pk + 1.2, length - 3.0)     # the closing line that asks for a comment, a share or a like; the top pill is free by then
    if cfg()["brand"].get("ask", True) and clip.get("ask") and length - ask >= 1.5:
        out.append({"kind": "ask", "text": clip["ask"], "start": round(ask, 2), "end": round(length - 0.05, 2)})
    return out


def mouth(audio, offset, fps=30):
    """Seconds where the narrator's mouth should be open, read from the loudness of the voice file itself."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32)
    n = 8000 // fps
    if len(x) < n:
        return []
    rms = np.sqrt((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1))
    opened = rms > 0.45 * np.percentile(rms, 90)
    out, start = [], None
    for i, o in enumerate(list(opened) + [False]):
        if o and start is None:
            start = i
        elif not o and start is not None:
            if i - start >= 2:                       # ignore single-frame flickers
                out.append([round(offset + start / fps, 3), round(offset + i / fps, 3)])
            start = None
    return out


def avatar_ready():
    A = cfg().get("avatar", {})
    folder = ROOT / A.get("folder", "brand/avatar")
    return bool(A.get("enabled")) and all((folder / f"{n}.png").exists() for n in ("idle", "talk", "watch", "shock", "laugh"))


def avatar_props(clip, length, voices):
    """voices = [(file, start_seconds, duration)] for each narrator line."""
    A = cfg().get("avatar", {})
    if not avatar_ready():
        return {"enabled": False}
    pub = MOTION / "public" / "avatar"
    pub.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / A.get("folder", "brand/avatar")).glob("*.png"):     # Remotion serves images from motion/public
        if not (pub / f.name).exists() or (pub / f.name).stat().st_mtime < f.stat().st_mtime:
            shutil.copy2(f, pub / f.name)
    pk = max(0.5, clip["peak"] - clip["in"])
    return {"enabled": True, "x": A.get("x", 30), "y": A.get("y", 1415), "size": A.get("size", 280),
            "talk": [r for f, s, d in voices for r in mouth(f, s)], "voice": [[round(s, 2), round(s + d, 2)] for f, s, d in voices],
            "watch": [round(max(0, pk - 2.2), 2), round(pk, 2)], "shock": [round(pk, 2), round(min(length, pk + 0.9), 2)],
            "laugh": [round(pk + 0.9, 2), round(min(length, pk + 3.6), 2)]}


def props(clip, words, length, vo, voices=()):
    c = cfg()
    b, L = c["brand"], c["layout"]
    layout = clip.get("layout") or L["default"]
    groups = overlay.chunks(words, clip["in"], clip["out"])
    chunks = []
    for i, g in enumerate(groups):
        nxt = groups[i + 1][0]["s"] if i + 1 < len(groups) else length
        emo = {k: v for k, v in (clip.get("emojis") or {}).items() if v not in BANNED_EMOJI}
        hit = next((emo[k] for w in g for k in [w["w"].lower().strip(".,!?'\"")] if k in emo), "")
        chunks.append({"start": round(g[0]["s"], 2), "end": round(min(g[-1]["e"] + 0.3, nxt), 2), "emoji": hit,
                       "words": [{"w": w["w"], "s": round(w["s"], 2)} for w in g]})
    marks = [b["handle"]] if b.get("handle") else []
    if b.get("credit") and clip.get("creator"):
        marks.append(f"clip: {clip['creator']}")
    game_top = L["focus_game_y"] if layout == "focus" else 420
    return {"fps": c["performance"]["fps"], "duration": round(length, 2), "peak": round(max(0.5, clip["peak"] - clip["in"]), 2),
            "accent": b["accent"], "hookText": b["hook_text"], "font": "Impact, 'Arial Black', 'DejaVu Sans', sans-serif",
            "hook": (clip.get("hook") or "").strip(), "hookY": overlay.hook_y(layout),
            "captionY": cfg().get("avatar", {}).get("caption_y", 1300) if avatar_ready() else L["caption_y"],
            "avatar": avatar_props(clip, length, voices),
            "beatY": game_top + 26, "marks": "   •   ".join(marks),
            "marksY": L["focus_game_y"] + L["focus_game_height"] + 44 if layout == "focus" else 1620,
            "chunks": chunks, "beats": beats(clip, length, vo)}


def build(clip, words, d, length, vo, voices=()):
    """Render the transparent overlay video; returns its path."""
    p = d / "motion_props.json"
    out = d / "overlay.mov"
    save_json(p, props(clip, words, length, vo, voices))
    out.unlink(missing_ok=True)
    run([MOTION / "node_modules" / ".bin" / "remotion", "render", "src/index.jsx", "Overlay", out, f"--props={p}",
         "--codec=prores", "--prores-profile=4444", "--pixel-format=yuva444p10le", "--image-format=png",
         "--concurrency=4", "--log=error"], heavy=True, capture=True, cwd=MOTION)
    return out


def sfx():
    """Synthesised effects (no samples, so nothing to license): every piece of text arrives with a sound.
    whoosh = hook, pop = the watch cue, hit = the peak itself, slam = the reaction text."""
    SFX.mkdir(parents=True, exist_ok=True)
    made = {"pop": "aevalsrc='0.7*sin(2*PI*(650+950*exp(-t*38))*t)*exp(-t*26)':d=0.2:s=48000",
            "slam": "aevalsrc='0.9*sin(2*PI*(64+240*exp(-t*28))*t)*exp(-t*8)+0.25*(random(0)*2-1)*exp(-t*30)':d=0.55:s=48000",
            "whoosh": ("anoisesrc=d=0.5:c=pink:a=0.9,bandpass=f=1700:w=1500,afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.33:d=0.17"),
            "hit": ("aevalsrc='0.9*sin(2*PI*(46+90*exp(-t*14))*t)*exp(-t*5.5)':d=0.8:s=48000")}
    for name, graph in made.items():
        f = SFX / f"{name}.wav"
        if not f.exists():
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", graph, "-ar", "48000", "-ac", "2", f])
    return {name: SFX / f"{name}.wav" for name in made}
