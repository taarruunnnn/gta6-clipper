"""Command line: python -m clipper <command>

  find <url-or-file> [--top N]     score a video/VOD, fetch + transcribe the best moments
  live <live-url> [--minutes N]    watch a live stream, cut a candidate whenever chat explodes
  streams [game]                   who is live on Twitch right now, by viewers (default: GTA VI)
  radar <twitch-login> [--week]    what viewers are clipping on a channel right now, by views
  list                             all clips and their status
  set <clip-id> [--hook ..] [--title ..] [--caption ..] [--hashtags ..] [--layout ..] [--in S] [--out S]
  copy <clip-id>                   draft hook / title / caption with Claude
  render <clip-id|all>             make the finished vertical clip
  publish <clip-id> [platforms..]  upload (same as the dashboard's Post button)
  youtube-login                    connect your YouTube channel (one time)
  keywords                         what people search for the game, ranked by views per upload today
  topic-videos [--count N]         clip today's top video for each searched topic that has no clip yet (off by default)
  hunt                             look for searched topics (hidden places, glitches ...) in streams already transcribed
  topics                           the topic database: searched moments, demand, and our clips for each
  learn                            pull view counts for posted clips and report what is working
  auto [--once] [--no-live]        hands-off: discover, clip, judge, render, post, learn (loops)
  judge <clip-id>                  score a candidate and write its copy
  cleanup [--dry-run]              delete old footage (the loop does this by itself every few hours)
  autostart on|off|status          start the loop at login and restart it after a crash
  ui                               open the dashboard (review, edit, one-click post)
"""
import argparse, sys
from .util import all_clips, be_nice, fmt_t, load_clip, save_clip


def main():
    ap = argparse.ArgumentParser(prog="clipper", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("find"); p.add_argument("src"); p.add_argument("--top", type=int)
    p = sub.add_parser("live"); p.add_argument("url"); p.add_argument("--minutes", type=float, default=60)
    p = sub.add_parser("radar"); p.add_argument("login"); p.add_argument("--week", action="store_true")
    p = sub.add_parser("streams"); p.add_argument("game", nargs="?", default="Grand Theft Auto VI")
    sub.add_parser("list"); sub.add_parser("learn"); sub.add_parser("youtube-login"); sub.add_parser("keywords"); sub.add_parser("topics"); sub.add_parser("hunt")
    p = sub.add_parser("topic-videos"); p.add_argument("--count", type=int, default=3)
    p = sub.add_parser("cleanup"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("autostart"); p.add_argument("action", choices=["on", "off", "status"])
    p = sub.add_parser("set"); p.add_argument("id")
    for k in ("hook", "title", "caption", "hashtags", "layout"):
        p.add_argument("--" + k)
    p.add_argument("--views", type=int)
    p.add_argument("--in", dest="t_in", type=float); p.add_argument("--out", dest="t_out", type=float)
    p = sub.add_parser("auto"); p.add_argument("--once", action="store_true"); p.add_argument("--no-live", action="store_true")
    p = sub.add_parser("judge"); p.add_argument("id")
    p = sub.add_parser("copy"); p.add_argument("id")
    p = sub.add_parser("render"); p.add_argument("id")
    p = sub.add_parser("publish"); p.add_argument("id"); p.add_argument("platforms", nargs="*")
    p = sub.add_parser("ui"); p.add_argument("--port", type=int, default=8765); p.add_argument("--no-open", action="store_true")
    a = ap.parse_args()

    if a.cmd == "ui":
        from .server import serve
        return serve(a.port, not a.no_open)
    if a.cmd == "list":
        for c in all_clips():
            print(f"{c['id']:<52} {c['status']:<10} {c['out'] - c['in']:>3.0f}s  {c.get('hook') or c['text'][:50]}")
        return
    if a.cmd == "radar":
        from .sources import twitch_clips
        for k in twitch_clips(a.login, "LAST_WEEK" if a.week else "LAST_DAY", 25):
            off = k.get("videoOffsetSeconds")
            print(f"{k['viewCount']:>8} views  {k['durationSeconds']:>3}s  {'at ' + fmt_t(off) if off is not None else '':<12} {k['title'][:60]}  {k['url']}")
        return
    if a.cmd == "youtube-login":
        from .youtube import login
        return print(login())
    if a.cmd == "autostart":
        from . import autostart
        return print(getattr(autostart, a.action)())
    if a.cmd == "cleanup":
        from .cleanup import run
        run(dry=a.dry_run)
        return
    if a.cmd == "topic-videos":
        from . import auto
        from .util import save_json
        be_nice()
        st = auto.state()
        made = auto.topic_videos(st, print, a.count)
        save_json(auto.STATE, st)
        return print(f"{len(made)} candidates" if made or auto.cfg()["auto"].get("topic_videos") else "off: set topic_videos = true under [auto] in config.local.toml")
    if a.cmd == "hunt":
        from .moments import hunt
        be_nice()
        hunt()
        return
    if a.cmd == "topics":
        from .topics import table
        return print(table())
    if a.cmd == "keywords":
        from .seo import research
        print(f"{'search phrase':<38}{'median views':>13}{'top video':>13}{'uploads today':>15}")
        for r in research():
            print(f"{r['phrase']:<38}{r['median_views']:>13,}{r['top_views']:>13,}{r['uploads_today']:>15}")
        return
    if a.cmd == "learn":
        from .learn import refresh
        return print(refresh())
    if a.cmd == "streams":
        from .sources import twitch_gql
        q = """query($g:String!){game(name:$g){streams(first:20){edges{node{viewersCount title broadcaster{login}}}}}}"""
        game = (twitch_gql(q, {"g": a.game}).get("data") or {}).get("game") or {}
        for e in (game.get("streams") or {}).get("edges", []):
            s = e["node"]
            print(f"{s['viewersCount']:>8} viewers  https://www.twitch.tv/{s['broadcaster']['login']:<24} {s['title'][:60]}")
        return
    if a.cmd == "set":
        c = load_clip(a.id)
        for k in ("hook", "title", "caption", "hashtags", "layout"):
            if getattr(a, k) is not None:
                c[k] = getattr(a, k)
        if a.views is not None:
            import time
            c.setdefault("stats", []).append({"at": time.time(), "age_h": None, "by": {"manual": {"views": a.views}}})
        if a.t_in is not None: c["in"] = a.t_in
        if a.t_out is not None: c["out"] = a.t_out
        save_clip(c)
        return print("saved", a.id)
    be_nice()
    if a.cmd == "find":
        from .moments import find
        clips = find(a.src, a.top)
        print(f"\n{len(clips)} candidates. Next: python -m clipper ui")
    elif a.cmd == "live":
        from .live import watch
        watch(a.url, a.minutes)
    elif a.cmd == "auto":
        from .auto import loop
        loop(a.once, not a.no_live)
    elif a.cmd == "judge":
        from .judge import judge
        judge(a.id)
    elif a.cmd == "copy":
        from .copywriter import draft
        print(draft(a.id))
    elif a.cmd == "render":
        from .render import render
        for cid in ([c["id"] for c in all_clips()] if a.id == "all" else [a.id]):
            render(cid)
    elif a.cmd == "publish":
        from .publish import publish
        print(publish(a.id, a.platforms or None))


if __name__ == "__main__":
    sys.exit(main())
