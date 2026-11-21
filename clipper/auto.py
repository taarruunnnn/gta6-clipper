"""Hands-off mode: find what is trending, clip it, judge it, render it, post it, learn from it.

Every cycle:
  1. Twitch: top live streams of the game -> the clips their viewers made today (already-proven moments),
     and a live watcher on the biggest streams that cuts on chat spikes.
  2. YouTube: the most-viewed recent long videos / finished streams for your search terms.
  3. Judge every new candidate (Claude: frames + transcript). Rejects never get rendered.
  4. Render the keepers.
  5. Post the best one if the daily cap and the gap since the last post allow it.
  6. Every few hours: pull view counts, and if a post was taken down, block that creator and tell you.
State lives in jobs/auto_state.json. Stop with Ctrl+C.
"""
import json, re, subprocess, sys, time
from . import publish, sources
from .util import JOBS, ROOT, all_clips, cfg, job_dir, load_clip, load_json, next_clip_number, save_clip, save_json, yt_search_today, ytdlp

STATE = JOBS / "auto_state.json"
watchers = {}   # login -> Popen


def state():
    return load_json(STATE, None) or {"seen": [], "posted": [], "last_learn": 0, "blocked": {}}


def notify(msg):
    print("NOTIFY:", msg)
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(msg)} with title \"Clipper\""], check=False)


def blocked(creator, st):
    names = {b.lower() for b in cfg()["auto"].get("blocklist", [])} | {b.lower() for b in st["blocked"]}
    return (creator or "").lower() in names


def live_streams():
    A = cfg()["auto"]
    q = """query($g:String!){game(name:$g){streams(first:30){edges{node{viewersCount language broadcaster{login displayName}}}}}}"""
    game = (sources.twitch_gql(q, {"g": A["game"]}).get("data") or {}).get("game") or {}
    out = []
    for e in (game.get("streams") or {}).get("edges", []):
        s = e["node"]
        if s["viewersCount"] >= A["min_viewers"] and (not A["languages"] or s.get("language") in A["languages"]) and s.get("broadcaster"):
            out.append({"login": s["broadcaster"]["login"], "name": s["broadcaster"]["displayName"], "viewers": s["viewersCount"]})
    return out


def from_viewer_clips(streams, st, log):
    """Today's most-viewed viewer clips of the game across Twitch: download, transcribe, make a candidate."""
    from .moments import make_candidate
    A = cfg()["auto"]
    try:
        pool = sources.twitch_game_clips(A["game"], A["languages"], 60)
    except Exception as e:
        return log(f"  game clips lookup failed: {e}")
    pool = [k for k in pool if k["slug"] not in st["seen"] and k["viewCount"] >= A["min_clip_views"] and k["durationSeconds"] >= 12
            and not blocked(k["broadcaster"]["displayName"], st) and not blocked(k["broadcaster"]["login"], st)]
    pool.sort(key=lambda k: -k["viewCount"])
    log(f"{len(pool)} new viewer clips today, taking the top {min(len(pool), A['clips_per_cycle'])}")
    for k in pool[:A["clips_per_cycle"]]:
        st["seen"].append(k["slug"])
        who, views = k["broadcaster"], k["viewCount"]
        try:
            d = job_dir(f"https://www.twitch.tv/{who['login']}")
            cd = d / "clips" / str(next_clip_number(d))
            cd.mkdir(parents=True)
            ytdlp("-f", "b[height<=1080]/b", "-o", str(cd / "src.mp4"), k["url"])
            meta = {"title": k["title"], "uploader": who["displayName"]}
            make_candidate(k["url"], d, meta, max(6.0, k["durationSeconds"] - 8), [f"viewer clip {views} views"], float(views), log,
                           section=cd / "src.mp4", sec_start=0.0)
        except Exception as e:
            log(f"  could not use clip {k['slug']}: {e}")


def from_youtube(st, log):
    """Most-viewed recent long video for the search terms that we have not processed yet."""
    from .moments import find
    A, C = cfg()["auto"], cfg()["performance"]
    pool = []
    from . import topics
    hunt = [p for p, _ in topics.moments(only_open=True)[:A["topics_per_cycle"]]]   # searched moments we have no clip for yet
    if hunt:
        log("hunting topics: " + ", ".join(hunt))
    for q in A["youtube_queries"] + hunt:
        try:
            found = yt_search_today(q)
        except Exception as e:
            log(f"  youtube search failed: {e}")
            continue
        for v in found:
            dur, views = v.get("duration") or 0, v.get("view_count") or 0
            who = v.get("channel") or v.get("uploader") or ""
            if (v["id"] not in st["seen"] and not blocked(who, st) and views >= A["youtube_min_views"]
                    and A["youtube_min_minutes"] * 60 <= dur <= C["max_source_minutes"] * 60):
                pool.append((views, v))
    pool.sort(key=lambda x: -x[0])
    for views, v in pool[:5]:
        url = f"https://www.youtube.com/watch?v={v['id']}"
        st["seen"].append(v["id"])
        try:
            if A["youtube_streams_only"] and not sources.info(url).get("was_live"):
                continue   # edited uploads are someone's finished work, not raw stream footage
            log(f"youtube: {v.get('title')} ({views:,} views)")
            find(url, top=A["clips_per_video"], log=log)
        except Exception as e:
            log(f"  could not process: {e}")
        return


def watch_live(streams, st, log):
    A = cfg()["auto"]
    for k in [k for k, p in watchers.items() if p.poll() is not None]:
        del watchers[k]
    for s in streams:
        if len(watchers) >= A["live_watchers"]:
            break
        if s["login"] not in watchers and not blocked(s["name"], st):
            log(f"watching live: {s['name']} ({s['viewers']:,} viewers)")
            watchers[s["login"]] = subprocess.Popen(
                [sys.executable, "-m", "clipper", "live", f"https://www.twitch.tv/{s['login']}", "--minutes", str(A["live_minutes"])],
                cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def process_candidates(st, log):
    from .judge import judge
    from .render import render
    for c in all_clips():
        if c["status"] != "candidate" or blocked(c.get("creator"), st):
            continue
        try:
            if "judge" not in c and not judge(c["id"], log):
                continue
            render(c["id"], log, use_motion=False)   # cheap preview; the motion version is made at post time
        except Exception as e:
            log(f"  failed on {c['id']}: {e}")


def post_next(st, log):
    A = cfg()["auto"]
    if A["mode"] != "auto":
        return
    now = time.time()
    today = [t for t in st["posted"] if time.localtime(t)[:3] == time.localtime(now)[:3]]
    if len(today) >= A["daily_cap"] or (st["posted"] and now - max(st["posted"]) < A["min_gap_minutes"] * 60):
        return
    ready = [c for c in all_clips() if c["status"] == "rendered" and c.get("judge") and not c.get("posts")
             and now - c["created"] < A["max_age_hours"] * 3600 and not blocked(c.get("creator"), st)]
    if not ready:
        return
    acct = publish.accounts()
    if not acct["configured"]:
        return log("clips are ready but nothing is connected to post to (run: python -m clipper youtube-login)")
    c = max(ready, key=lambda c: (c["judge"]["score"] + (0.5 if c.get("topic") else 0), c["created"]))   # searched topics go first
    try:
        if cfg()["brand"].get("motion"):
            from .render import render
            render(c["id"], log)   # full motion style, only for the clip that is actually going out
        result = publish.publish(c["id"], log=log)
    except Exception as e:
        return log(f"  post failed: {e}")
    c = load_clip(c["id"])
    if any(v.get("ok") for v in c.get("posts", {}).values()):
        st["posted"].append(now)
        notify(f"Posted: {c.get('hook')}")
    else:   # do not retry the same clip forever
        c["status"] = "candidate"; c["posts"] = c.get("posts") or {"error": {"ok": False, "error": result, "url": "", "at": now}}
        save_clip(c)
        notify(f"Post failed: {result[:80]}")


def learn_and_guard(st, log):
    """Refresh stats; a post that was taken down blocks its creator and alerts you."""
    from .learn import refresh
    if time.time() - st["last_learn"] < cfg()["auto"]["learn_hours"] * 3600:
        return
    st["last_learn"] = time.time()
    refresh(log)
    try:
        from .seo import research
        research(log=log)   # refresh keyword demand a few times a day
    except Exception as e:
        log(f"keyword research failed: {e}")
    for c in all_clips():
        for p, v in (c.get("posts") or {}).items():
            if v.get("removed") and not v.get("handled"):
                st["blocked"][c.get("creator") or "?"] = f"post removed on {p}: {v.get('removed')}"
                v["handled"] = True
                save_clip(c)
                notify(f"A {c.get('creator')} clip was taken down on {p}. That creator is now blocked.")


def cycle(log=print, live=True):
    st = state()
    try:
        streams = live_streams()
        log(f"{len(streams)} eligible live streams" + (": " + ", ".join(s["name"] for s in streams[:5]) if streams else ""))
        if live:
            watch_live(streams, st, log)
        from_viewer_clips(streams, st, log)
    except Exception as e:
        log(f"twitch step failed: {e}")
    for step in (from_youtube, process_candidates, post_next, learn_and_guard):
        save_json(STATE, st)   # survive a crash without re-clipping the same sources
        step(st, log)
    st["seen"] = st["seen"][-3000:]
    save_json(STATE, st)


def loop(once=False, live=True):
    JOBS.mkdir(exist_ok=True)
    import fcntl
    lock = open(JOBS / "auto.lock", "w")
    try:   # only one loop at a time, or two would clip and post the same things
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return print("the loop is already running; not starting a second one")
    log = lambda m: print(f"{time.strftime('%m-%d %H:%M:%S')}  {m}", flush=True)
    try:
        while True:
            log("--- cycle ---")
            cycle(log, live and not once)
            if once:
                break
            time.sleep(cfg()["auto"]["cycle_minutes"] * 60)
    except KeyboardInterrupt:
        pass
    finally:
        for p in watchers.values():
            p.terminate()
