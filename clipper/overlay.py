"""Draws the branded layer: hook box, word-by-word captions with the spoken word highlighted, watermark.

Produces transparent 1080x1920 PNGs plus an ffconcat timeline, so ffmpeg overlays one input and a new
image is decoded only when the caption changes.
"""
import re
from PIL import Image, ImageDraw, ImageFont
from .util import cfg

W, H = 1080, 1920
MASK = re.compile(r"(?i)\b(f)uck|\b(s)hit|\b(b)itch|\b(a)sshole|\b(d)ick\b|\b(c)unt|\b(p)ussy|\b(n)igg\w*")


FONTS = ["/System/Library/Fonts/Supplemental/Impact.ttf", "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
         "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]


def font(size):
    for path in [cfg()["brand"].get("font", "")] + FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def clean(word):
    if cfg()["brand"].get("mask_profanity", True):
        word = MASK.sub(lambda m: next(g for g in m.groups() if g) + "*" * (len(m.group(0)) - 1), word)
    return word.upper()


def chunks(words, t_in, t_out):
    """Groups of up to 3 words / 16 characters, broken at pauses and sentence ends."""
    out, cur = [], []
    for w in words:
        if w["e"] <= t_in or w["s"] >= t_out:
            continue
        w = {"w": clean(w["w"]), "s": max(0.0, w["s"] - t_in), "e": min(t_out, w["e"]) - t_in}
        if cur and (len(cur) >= 3 or len(" ".join(x["w"] for x in cur)) + len(w["w"]) > 16 or w["s"] - cur[-1]["e"] > 0.5
                    or re.search(r"[.!?,]$", cur[-1]["w"])):
            out.append(cur); cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")   # the still-image style cannot draw colour emoji


def hook_y(layout):
    L = cfg()["layout"]
    return {"focus": L["focus_game_y"] // 2 + 60, "stack": L["stack_cam_height"], "fill": 300}.get(layout, 300)


def draw_static(clip):
    b, L = cfg()["brand"], cfg()["layout"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    hook = EMOJI.sub("", clip.get("hook") or "").strip().upper()
    if hook:
        size = b["hook_size"]
        while True:  # shrink until it fits 3 lines
            f = font(size)
            lines, cur = [], ""
            for word in hook.split():
                t = (cur + " " + word).strip()
                if d.textlength(t, font=f) > 900 and cur:
                    lines.append(cur); cur = word
                else:
                    cur = t
            lines.append(cur)
            if len(lines) <= 3 or size <= 44:
                break
            size -= 6
        lh = int(size * 1.18)
        bw = int(max(d.textlength(l, font=f) for l in lines)) + 64
        bh = lh * len(lines) + 40
        x0, y0 = (W - bw) // 2, max(130, hook_y(clip.get("layout")) - bh // 2)
        d.rounded_rectangle([x0 + 6, y0 + 8, x0 + bw + 6, y0 + bh + 8], 26, fill=(0, 0, 0, 150))
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], 26, fill=b["accent"])
        for i, l in enumerate(lines):
            d.text((W // 2, y0 + 20 + i * lh + lh // 2), l, font=f, fill=b["hook_text"], anchor="mm")
    marks = [b["handle"]] if b.get("handle") else []
    if b.get("credit") and clip.get("creator"):
        marks.append(f"clip: {clip['creator']}")
    if marks:
        y = L["focus_game_y"] + L["focus_game_height"] + 44 if clip.get("layout") == "focus" else 1620
        d.text((W // 2, y), "   •   ".join(marks), font=font(34), fill=(255, 255, 255, 190), anchor="mm",
               stroke_width=3, stroke_fill=(0, 0, 0, 170))
    return img


def draw_caption(base, chunk, active):
    b = cfg()["brand"]
    img = base.copy()
    d = ImageDraw.Draw(img)
    size = b["caption_size"]
    f = font(size)
    while size > 50 and sum(d.textlength(w["w"], font=f) for w in chunk) + (len(chunk) - 1) * size * 0.28 > 980:
        size -= 6; f = font(size)
    gap = size * 0.28
    x = (W - (sum(d.textlength(w["w"], font=f) for w in chunk) + (len(chunk) - 1) * gap)) / 2
    y = cfg()["layout"]["caption_y"]
    for i, w in enumerate(chunk):
        d.text((x, y), w["w"], font=f, anchor="lm", fill=b["accent"] if i == active else b["caption_fill"],
               stroke_width=max(6, size // 10), stroke_fill=b["caption_stroke"])
        x += d.textlength(w["w"], font=f) + gap
    return img


def build(clip, words, out_dir):
    """Write PNG states + timeline.txt; returns the timeline path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    length = clip["out"] - clip["in"]
    base = draw_static(clip)
    base.save(out_dir / "base.png", compress_level=1)
    events, k = [(0.0, "base.png")], 0   # (start, file)
    groups = chunks(words, clip["in"], clip["out"])
    for ci, ch in enumerate(groups):
        for i, w in enumerate(ch):
            name = f"c{k:04d}.png"; k += 1
            draw_caption(base, ch, i).save(out_dir / name, compress_level=1)
            events.append((w["s"], name))
        end = ch[-1]["e"] + 0.25
        if ci + 1 == len(groups) or groups[ci + 1][0]["s"] > end + 0.3:   # pause: clear the caption
            events.append((end, "base.png"))
    states, last = [], -1.0
    for s, name in events:
        s = min(length, max(s, last + 0.04))
        states.append((name, s)); last = s
    lines = ["ffconcat version 1.0"]
    for (name, s), nxt in zip(states, states[1:] + [(None, length + 0.5)]):
        lines += [f"file '{name}'", f"duration {max(0.04, nxt[1] - s):.3f}"]
    lines.append("file 'base.png'")   # concat needs the last image repeated to honour its duration
    tl = out_dir / "timeline.txt"
    tl.write_text("\n".join(lines) + "\n")
    return tl
