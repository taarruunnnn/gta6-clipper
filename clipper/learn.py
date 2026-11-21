"""The learning loop: measure every posted clip, see what the winners share, feed that back in.

1. `learn` reads public view / like / comment counts for each post URL (no API keys) and stores a snapshot.
2. The report groups clips by what we control: layout, length, hook length, what signal found the moment,
   streamer, hour posted.
3. The copywriter is shown your best and worst hooks by views, so new hooks follow what works on YOUR page.
Judge a clip on its 72 h number, and change one thing at a time, or the comparison means nothing.
"""
import json, re, statistics, time
from .util import all_clips, cfg as util_cfg, load_clip, save_clip, ytdlp


def views(c):
    """Best view count across platforms from the latest snapshot."""
    s = c.get("stats") or []
    return max((v.get("views") or 0 for v in s[-1]["by"].values()), default=0) if s else 0


def refresh(log=print):
    n = 0
    for c in all_clips():
        urls = {p: v["url"] for p, v in (c.get("posts") or {}).items() if v.get("ok") and v.get("url")}
        if not urls:
            continue
        by = {}
        for p, url in urls.items():
            r = ytdlp("-J", "--skip-download", url, capture=True, check=False)
            try:
                j = json.loads(r.stdout)
                by[p] = {"views": j.get("view_count"), "likes": j.get("like_count"), "comments": j.get("comment_count")}
            except Exception:
                err = r.stderr or ""
                if "private" in err.lower() or util_cfg().get("youtube", {}).get("privacy", "public") != "public":
                    pass   # our own private/unlisted upload: not readable publicly, and not a takedown
                elif re.search(r"removed|terminated|copyright|violat|not available|no longer available", err, re.I):
                    c2 = load_clip(c["id"]); c2["posts"][p]["removed"] = (r.stderr or "").strip()[-120:]; save_clip(c2)
                    log(f"  {p} post for {c['id']} looks taken down")
                else:
                    log(f"  could not read {p} stats for {c['id']} (enter them by hand: set {c['id']} --views N)")
        if by:
            c = load_clip(c["id"])
            posted = min(v["at"] for v in c["posts"].values() if v.get("ok"))
            c.setdefault("stats", []).append({"at": time.time(), "age_h": round((time.time() - posted) / 3600, 1), "by": by})
            save_clip(c)
            n += 1
    log(f"stats refreshed for {n} clip(s)")
    return report()


def features(c):
    posted = [v["at"] for v in (c.get("posts") or {}).values() if v.get("ok")]
    L = c["out"] - c["in"]
    return {"layout": c.get("layout", "?"),
            "length": "under 20s" if L < 20 else "20-30s" if L < 30 else "30s+",
            "hook words": "1-4" if len((c.get("hook") or "").split()) <= 4 else "5+",
            "found by": (c.get("why") or ["?"])[0].split(" ")[0] + " " + ((c.get("why") or ["?"])[0].split(" ") + [""])[1],
            "streamer": c.get("creator") or "?",
            "posted at": f"{time.localtime(min(posted)).tm_hour // 4 * 4:02d}:00+" if posted else "?"}


def report():
    clips = [c for c in all_clips() if c.get("stats")]
    if not clips:
        return "No stats yet. Post some clips, then run `learn` after 24 h and again after 72 h."
    lines = [f"{len(clips)} measured clips, median {statistics.median(views(c) for c in clips):,.0f} views", ""]
    for key in ("layout", "length", "hook words", "found by", "streamer", "posted at"):
        groups = {}
        for c in clips:
            groups.setdefault(features(c)[key], []).append(views(c))
        lines.append(key.upper())
        for g, v in sorted(groups.items(), key=lambda kv: -statistics.median(kv[1])):
            lines.append(f"  {g:<22} median {statistics.median(v):>9,.0f}   ({len(v)} clips)")
    best = sorted(clips, key=views, reverse=True)[:5]
    lines += ["", "TOP HOOKS"] + [f"  {views(c):>9,}  {c.get('hook')}" for c in best]
    if len(clips) < 20:
        lines += ["", f"Only {len(clips)} clips measured: treat differences as hints until you have 20+."]
    return "\n".join(lines)


def hook_examples():
    """Best and worst hooks so far, for the copywriter prompt."""
    clips = sorted([c for c in all_clips() if c.get("stats") and c.get("hook")], key=views, reverse=True)
    if len(clips) < 4:
        return ""
    k = min(5, len(clips) // 2)
    fmt = lambda cs: "\n".join(f"  - \"{c['hook']}\" ({views(c):,} views)" for c in cs)
    return (f"\nOn this page these hooks performed best:\n{fmt(clips[:k])}\nand these performed worst:\n{fmt(clips[-k:])}\n"
            "Lean toward what the best ones have in common.\n")
