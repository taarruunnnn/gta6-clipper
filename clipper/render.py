"""Turn a candidate into the finished 1080x1920 clip.

CPU savers: hardware decode + hardware H.264 encode (Apple media engine), blur done on a tiny copy of the
frame, 30 fps, and the whole ffmpeg process runs at background priority on the efficiency cores.
"""
import sys
from . import motion, overlay
from .util import cfg, clip_dir, duration, load_clip, load_json, run, save_clip, video_encoder

W, H = 1080, 1920


def graph(clip, length, ducks=(), extras=()):
    c = cfg()
    L, b, fps = c["layout"], c["brand"], c["performance"]["fps"]
    layout = clip.get("layout") or L["default"]
    pk = max(0.5, clip["peak"] - clip["in"])
    zf = float(b.get("punch_zoom", 0))
    ze = f"1+{zf}*min(1,max(0,(it-{pk:.2f})/0.18))*min(1,max(0,({pk + 1.8:.2f}-it)/0.35))"

    def punch(w, h):
        return f",zoompan=z='{ze}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={w}x{h}:fps={fps}" if zf > 0 else ""

    if layout == "fill":
        g = f"[0:v]fps={fps},scale=-2:{H},crop={W}:{H}{punch(W, H)}[base];"
    elif layout == "stack":
        x, y, w, h = L["stack_cam"]
        ch = L["stack_cam_height"]
        gh = H - ch
        g = (f"[0:v]fps={fps},split[a][b];"
             f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{ch}:force_original_aspect_ratio=increase,crop={W}:{ch}[cam];"
             f"[b]scale=-2:{gh},crop={W}:{gh}{punch(W, gh)}[game];[cam][game]vstack[base];")
    else:  # focus: square gameplay over a blurred, darkened copy of itself; text sits in the bands
        gh, gy = L["focus_game_height"], L["focus_game_y"]
        g = (f"[0:v]fps={fps},split[a][b];"
             f"[a]scale=108:192:force_original_aspect_ratio=increase,crop=108:192,gblur=sigma=5,"
             f"scale={W}:{H}:flags=bilinear,eq=brightness=-0.15:saturation=1.2[bg];"
             f"[b]scale=-2:{gh},crop='min(iw,{W})':{gh}{punch(W, gh)}[fg];"
             f"[bg][fg]overlay=(W-w)/2:{gy}[base];")
    g += "[base][1:v]overlay=0:0:eof_action=pass[ov];"
    if b.get("progress_bar", True):
        color = b["accent"].replace("#", "0x")
        g += (f"color=c={color}:s={W}x10:r={fps}:d={length:.2f}[bar];"
              f"[ov][bar]overlay=x='-W+W*t/{length:.2f}':y=H-10:shortest=1[v];")
    else:
        g += "[ov]null[v];"
    # audio: stream normalised to -14 LUFS, ducked wherever our narrator speaks; narrator and effects mixed on top
    a0 = "[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    if ducks:
        on = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in ducks)
        a0 += f",volume='if(gt({on},0),0.18,1)':eval=frame"
    if not extras:
        return g + a0 + "[a]"
    g += a0 + "[a0];"
    for idx, delay, gain, voice in extras:
        norm = ",loudnorm=I=-13:TP=-1.5" if voice else ""
        g += f"[{idx}:a]aresample=48000{norm},volume={gain},adelay={int(delay * 1000)}:all=1[x{idx}];"
    g += "[a0]" + "".join(f"[x{i[0]}]" for i in extras) + f"amix=inputs={len(extras) + 1}:duration=first:normalize=0,alimiter=limit=0.95[a]"
    return g


def speak(text, path):
    """Narrator line to an audio file; returns (file, seconds) or (None, 0)."""
    V = cfg().get("commentary", {})
    path.unlink(missing_ok=True)
    try:   # neural voice (needs internet); falls back to the built-in Mac voice
        run([sys.executable, "-m", "edge_tts", "--voice", V.get("voice", "en-GB-RyanNeural"), f"--rate={V.get('rate', '+12%')}",
             f"--pitch={V.get('pitch', '+0Hz')}", "--text", text, "--write-media", path], capture=True, timeout=40)
    except Exception:
        if sys.platform != "darwin":
            return None, 0.0                      # no offline voice outside macOS: the clip simply has no narrator
        path = path.with_suffix(".aiff")
        run(["say", "-v", V.get("fallback_voice", "Daniel"), "-o", path, text], check=False)
    return (path, duration(path)) if path.exists() and path.stat().st_size > 0 else (None, 0.0)


def render(clip_id, log=print, use_motion=None, out_name="out.mp4"):
    clip = load_clip(clip_id)
    d = clip_dir(clip_id)
    c = cfg()
    length = clip["out"] - clip["in"]
    pk = max(0.5, clip["peak"] - clip["in"])
    words = load_json(d / "words.json", [])
    if use_motion is None:
        use_motion = bool(c["brand"].get("motion")) and bool(motion.available())
    V = c.get("commentary", {})
    inputs, ducks, extras, vo, voices = [], [], [], 0.0, []     # extra audio inputs start at index 2 (0 = footage, 1 = text layer)

    def add(path, delay, gain, voice=False):
        inputs.extend(["-i", path])
        extras.append((2 + len(extras), delay, gain, voice))

    if V.get("enabled") and clip.get("commentary"):
        f, dur = speak(clip["commentary"], d / "vo.mp3")
        if f:
            vo = dur + 0.25
            add(f, 0.25, 1.0, True)
            voices.append((f, 0.25, dur))
            ducks.append((0, vo + 0.4))
            words = [w for w in words if w["s"] >= clip["in"] + vo]   # no captions for speech the narrator covers
    if use_motion:
        if V.get("enabled") and clip.get("outro"):   # narrator's reaction after the payoff, if there is room for it
            f, dur = speak(clip["outro"], d / "vo2.mp3")
            start = max(pk + 2.6, length - dur - 0.3)
            if f and start + dur <= length and start > vo + 1:
                add(f, start, 1.0, True)
                voices.append((f, start, dur))
                ducks.append((start - 0.15, start + dur + 0.2))
                words = [w for w in words if not (clip["in"] + start - 0.2 <= w["s"] <= clip["in"] + start + dur)]
        if c["brand"].get("sound_effects", True):
            fx = motion.sfx()
            add(fx["whoosh"], 0.0, 0.5)                       # hook punches in
            add(fx["hit"], max(0.0, pk - 0.03), 0.6)          # the moment itself
            for b in motion.beats(clip, length, vo):          # each piece of commentary text lands with a sound
                add(fx["slam" if b["kind"] == "payoff" else "pop"], b["start"], 0.7)
        text_layer = ["-i", motion.build(clip, words, d, length, vo, voices)]
    else:
        text_layer = ["-f", "concat", "-safe", "0", "-i", overlay.build(clip, words, d / "overlay")]
    out = d / out_name
    hw, enc = video_encoder()
    cmd = ["ffmpeg", "-y", "-v", "error", *hw,
           "-ss", f"{clip['in']:.2f}", "-t", f"{length:.2f}", "-i", d / "src.mp4", *text_layer, *inputs,
           "-filter_complex", graph(clip, length, ducks, extras), "-map", "[v]", "-map", "[a]", "-t", f"{length:.2f}",
           *enc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    log(f"rendering {clip_id} ({length:.0f}s, {clip.get('layout')}, {'motion' if use_motion else 'classic'})")
    run(cmd, heavy=True, capture=True)
    (d / "overlay.mov").unlink(missing_ok=True)     # the transparent layer is large and no longer needed
    if out_name != "out.mp4":
        return out
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(length - 0.5, pk):.2f}", "-i", out,
         "-frames:v", "1", "-vf", "scale=540:-2", d / "thumb.jpg"], check=False)
    clip = load_clip(clip_id)
    clip["status"] = "rendered" if clip.get("status") != "published" else "published"
    clip["output"] = "out.mp4"
    save_clip(clip)
    log(f"done: {out}")
    return out
