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
        subprocess.run(["osascript", "-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "Clipper"',
                        "-e", "end run", str(msg)], check=False, capture_output=True)


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
    # Creators on the watchlist: a few per cycle, in turn. Their own channel only (never a fan re-upload), and a
    # lower view bar than the open search, because we already know the streamer draws a crowd.
    names, plain = A.get("creators", []), lambda t: re.sub(r"[^a-z0-9]", "", (t or "").lower())
    start = st.get("creator_cursor", 0) % max(1, len(names))
    turn = (names[start:] + names[:start])[:A.get("creators_per_cycle", 4)]
    st["creator_cursor"] = start + len(turn)
    for name in turn:
        try:
            found = yt_search_today(A.get("creator_query", "{name} gta 6").format(name=name), 10)
        except Exception as e:
            log(f"  search failed for {name}: {str(e)[:60]}")
            continue
        for v in found:
            dur, views = v.get("duration") or 0, v.get("view_count") or 0
            if (plain(v.get("channel") or v.get("uploader")) == plain(name) and v["id"] not in st["seen"] and not blocked(name, st)
                    and views >= A.get("creator_min_views", 100000)
                    and A["youtube_min_minutes"] * 60 <= dur <= C["max_source_minutes"] * 60):
                pool.append((views, v))
    if turn:
        log("watchlist checked: " + ", ".join(turn))
    pool.sort(key=lambda x: -x[0])
    for views, v in pool[:5]:
        url = f"https://www.youtube.com/watch?v={v['id']}"
        st["seen"].append(v["id"])
        save_json(STATE, st)
        try:
            meta = sources.info(url)
            if A["youtube_streams_only"] and not meta.get("was_live"):
                continue   # edited uploads are someone's finished work, not raw stream footage
            lang = (meta.get("language") or "").split("-")[0].upper()
            if A["languages"] and lang and lang not in A["languages"]:
                log(f"  skipped (language {lang}): {v.get('title')}")
                continue
            log(f"youtube: {v.get('title')} ({views:,} views)")
            find(url, top=A["clips_per_video"], log=log)
        except Exception as e:
            log(f"  could not process: {e}")
        return


GAME_WORDS = {"gta", "6", "vi", "grand", "theft", "auto", "in", "the", "of", "to", "a", "for", "and", "all", "how"}


def about(title, topic):
    """Is this video's title about the searched topic? Every distinctive word of the search must be in the title
    ("easter eggs" matches "Easter Egg"). Without this, the day's biggest stream wins every search."""
    words = [w.rstrip("s") for w in re.findall(r"[a-z0-9]+", topic.lower()) if w not in GAME_WORDS]
    have = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (title or "").lower())}
    return bool(words) and all(w in have for w in words)


def topic_videos(st, log, count=None):
    """For the most-searched topics we have no clip for, take today's most-viewed video on that exact search and look
    inside it for the moment. Unlike stream discovery this includes edited videos, so it is off unless
    [auto] topic_videos = true: those are another creator's finished work, and the credit matters even more."""
    from . import topics
    from .moments import find_topic
    A, C = cfg()["auto"], cfg()["performance"]
    if not A.get("topic_videos"):
        return []
    made, done, seen_words = [], 0, []
    for topic, _ in topics.moments(only_open=True):
        words = frozenset(topic.split())
        if words in seen_words:                      # "money locations" and "locations for money" are one search
            continue
        seen_words.append(words)
        if done >= (count or A.get("topic_videos_per_cycle", 1)):
            break
        try:
            found = yt_search_today(topic, 25)
        except Exception as e:
            log(f"  search failed for {topic}: {str(e)[:60]}")
            continue
        pool = [v for v in found if v["id"] not in st["seen"] and (v.get("view_count") or 0) >= A.get("topic_min_views", 100000)
                and about(v.get("title"), topic)
                and 60 <= (v.get("duration") or 0) <= C["max_source_minutes"] * 60
                and not blocked(v.get("channel") or v.get("uploader") or "", st)]
        pool.sort(key=lambda v: -(v.get("view_count") or 0))
        if not pool:
            log(f"topic '{topic}': no video today with that in its title and enough views")
        for v in pool[:3]:
            url = f"https://www.youtube.com/watch?v={v['id']}"
            st["seen"].append(v["id"])
            save_json(STATE, st)
            try:
                lang = (sources.info(url).get("language") or "").split("-")[0].upper()
                if A["languages"] and lang and lang not in A["languages"]:
                    continue
                log(f"topic '{topic}': {v.get('title')} ({v.get('view_count'):,} views)")
                made += find_topic(url, topic, 2, log)
                done += 1
                break
            except Exception as e:
                log(f"  could not process: {str(e)[:100]}")
    return made


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
        if cfg().get("cleanup", {}).get("published_days", 7) <= 0:    # delete the local copy as soon as it is up
            from .cleanup import run as clean
            clean(log=log)
    elif re.search(r"invalid_grant|sign-in|signed in|expired|revoked", str(c.get("posts")) + result, re.I):
        c["posts"] = {}          # not the clip's fault: keep it queued and ask for a new sign-in
        save_clip(c)
        if now - st.get("warned_signin", 0) > 3 * 3600:
            st["warned_signin"] = now
            notify("Posting stopped: the YouTube sign-in expired. Renew it: python -m clipper youtube-login")
    else:   # do not retry the same clip forever
        c["status"] = "candidate"; c["posts"] = c.get("posts") or {"error": {"ok": False, "error": result, "url": "", "at": now}}
        save_clip(c)
        notify(f"Post failed: {result[:80]}")


def signin_guard(st, log):
    """Tell the user a day ahead that the YouTube sign-in is about to lapse, at most twice a day."""
    from . import youtube
    left = youtube.signin_days_left() if youtube.ready() else None
    if left is not None and left < 1.5 and time.time() - st.get("warned_signin", 0) > 12 * 3600:
        st["warned_signin"] = time.time()
        when = "has probably expired" if left <= 0 else f"expires in about {max(1, round(left * 24))} hours"
        notify(f"YouTube sign-in {when}. Renew it: python -m clipper youtube-login")
        log(f"sign-in warning sent ({left:.1f} days left)")


def tidy(st, log):
    """Free disk space once every few hours."""
    if time.time() - st.get("last_cleanup", 0) > cfg().get("cleanup", {}).get("every_hours", 6) * 3600:
        st["last_cleanup"] = time.time()
        from .cleanup import run
        run(log=log)


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
        from .moments import hunt
        hunt(log)           # new searched topics: look for them in streams we already transcribed
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
    # Post first, and again after the quick sources: a long stream can take half an hour to analyse and must never
    # hold up a clip that is ready to go.
    for step in (signin_guard, post_next, process_candidates, post_next, topic_videos, process_candidates, post_next,
                 from_youtube, process_candidates, post_next, learn_and_guard, tidy):
        save_json(STATE, st)   # survive a crash without re-clipping the same sources
        try:
            step(st, log)
        except Exception as e:
            log(f"{step.__name__} failed: {str(e)[:200]}")
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
    beat = [time.time()]          # time of the last sign of life; None while resting between cycles

    def log(m):
        beat[0] = time.time()
        print(f"{time.strftime('%m-%d %H:%M:%S')}  {m}", flush=True)

    def watchdog():
        """A stuck download or transcription once froze the loop for hours. If nothing has been logged for
        stall_minutes, stop with an error: the login service starts a fresh loop, and the source that hung is
        already marked as seen, so it is not tried again."""
        import os, threading
        limit = cfg()["auto"].get("stall_minutes", 40) * 60

        def check():
            while True:
                time.sleep(60)
                if beat[0] and time.time() - beat[0] > limit:
                    print(f"{time.strftime('%m-%d %H:%M:%S')}  no progress for {limit // 60} minutes: restarting the loop", flush=True)
                    for p in watchers.values():
                        p.terminate()
                    os._exit(1)
        threading.Thread(target=check, daemon=True).start()

    if not once:
        watchdog()
    try:
        while True:
            log("--- cycle ---")
            cycle(log, live and not once)
            if once:
                break
            beat[0] = None
            time.sleep(cfg()["auto"]["cycle_minutes"] * 60)
            beat[0] = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        for p in watchers.values():
            p.terminate()
