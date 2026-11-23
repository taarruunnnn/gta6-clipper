"""Titles and descriptions that rank, chosen from data instead of by hand.

Inputs, refreshed every few hours and cached:
  - what people are typing into YouTube search right now (autocomplete for the game and for the streamer)
  - the titles of today's most-viewed videos for the game, and the words they share
  - the titles of your own best and worst clips (once `learn` has numbers)
The judge writes three titles with this brief in front of it; score_title() then picks the winner by rule,
so the choice is consistent and measurable rather than taste.
"""
import json, re, time, urllib.parse, urllib.request
from collections import Counter
from .util import JOBS, all_clips, cfg, load_json, save_json, yt_search_today

CACHE = JOBS / "seo_cache.json"
STOP = set("the a an and or to of is it in i you that this on for with was are be so but not my me we he she they what just "
           "like at as if do get his her how why when from by vs new best part shorts short video".split())


def suggestions(seed):
    """YouTube's own search autocomplete: the phrases with real search volume."""
    url = "https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&hl=en&gl=US&q=" + urllib.parse.quote(seed)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as r:
            return [s for s in json.loads(r.read().decode("utf-8", "ignore"))[1] if isinstance(s, str)][:10]
    except Exception:
        return []


def market():
    """Search phrases + what the current top Shorts are titled. Cached for 6 hours."""
    c = load_json(CACHE)
    if c and time.time() - c["at"] < 6 * 3600:
        return c
    game = cfg()["auto"]["seo_seed"]
    sugg = suggestions(game) + suggestions(game + " funny") + suggestions(game + " how to")
    titles = []
    try:
        vids = [v for v in yt_search_today(game, 40) if v.get("view_count")]   # Shorts carry no view counts in search, so use all of today's winners
        titles = [(v["view_count"], v["title"]) for v in sorted(vids, key=lambda v: -v["view_count"])[:15]]
    except Exception:
        pass
    words = Counter()
    for views, t in titles:
        for w in set(re.findall(r"[a-z0-9']{3,}", t.lower())) - STOP:
            words[w] += 1
    c = {"at": time.time(), "suggestions": list(dict.fromkeys(sugg)), "top_titles": titles,
         "terms": [w for w, n in words.most_common(15) if n >= 2]}
    save_json(CACHE, c)
    return c


def own_titles():
    from .learn import views
    clips = sorted([c for c in all_clips() if c.get("stats") and c.get("title")], key=views, reverse=True)
    if len(clips) < 4:
        return ""
    k = min(4, len(clips) // 2)
    f = lambda cs: "; ".join(f"\"{c['title']}\" ({views(c):,})" for c in cs)
    return f"\nYour own best titles by views: {f(clips[:k])}\nYour worst: {f(clips[-k:])}\n"


def brief(creator):
    m = market()
    who = suggestions(f"{creator} {cfg()['auto']['seo_seed']}")[:5] if creator else []
    out = ["SEARCH DATA (use it, do not invent keywords):"]
    if top_keywords():
        out.append("Highest-opportunity searches today (many views per upload), prefer these when they fit the clip: "
                   + "; ".join(top_keywords()))
    if m["suggestions"]:
        out.append("People are searching: " + "; ".join(m["suggestions"][:14]))
    if who:
        out.append(f"Searches about this streamer: " + "; ".join(who))
    if m["top_titles"]:
        out.append("Most-viewed videos today: " + "; ".join(f"\"{t}\" ({v:,})" for v, t in m["top_titles"][:8]))
    if m["terms"]:
        out.append("Words the winners share: " + ", ".join(m["terms"]))
    return "\n".join(out) + own_titles()


SUFFIX = re.compile(r"(?i)[\s_]*(live|tv|ttv|gaming|plays|clips|two|more|hd|yt|official)$")


def short_name(creator):
    """The name viewers use: 'Kai Cenat Live' -> 'Kai Cenat', 'caseoh_' -> 'caseoh', 'ZerkaaPlays' -> 'Zerkaa'."""
    n = (creator or "").strip().strip("_")
    while True:
        m = SUFFIX.sub("", n).strip().strip("_")
        if m == n or len(m) < 3:
            return n
        n = m


def names(title, creator):
    """0 = the streamer is not in the title, 1 = somewhere in it, 2 = the title opens with them.
    Nicknames count when they are part of the handle: 'Speed' for IShowSpeed, 'Kai' for Kai Cenat."""
    squash = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    who = squash(short_name(creator))
    if len(who) < 3:
        return 0
    first = squash(re.sub(r"'s$", "", (title.split() or [""])[0]))
    if squash(title).startswith(who) or (len(first) >= 3 and first in who):
        return 2
    return 1 if who in squash(title) else 0


def score_title(title, creator="", topic=""):
    """Rule-based ranking score. Higher is better."""
    m, t, s = market(), title.lower(), 0.0
    game_words = cfg()["auto"]["seo_seed"].lower().split()
    by_name = cfg()["auto"].get("title_style", "name") == "name"
    if all(w in t for w in game_words):
        s += 3                                             # the game name must be findable
        if not by_name and t.find(game_words[0]) < 25:
            s += 1                                         # ... and early: titles get cut off in the feed
    named = names(title, creator)
    if by_name:                                            # how the clips that go viral are titled: "<Name> does X in GTA 6"
        s += (0, 3, 4)[named]
        if re.match(r"[^:]{0,32}:", title):
            s -= 2                                         # a keyword label in front ("GTA 6 Gameplay: ...") reads like a robot
    elif named:
        s -= 4                                             # search style: the streamer is credited on the video, not in the title
    s += min(3, sum(1 for w in m["terms"] if w in t))      # words the current winners share
    for phrase in top_keywords(8) + m["suggestions"]:      # overlap with a real search phrase, best opportunities first
        extra = [w for w in phrase.lower().split() if w not in game_words and len(w) > 2]
        if extra and all(w in t for w in extra):
            s += 2
            break
    if topic:                                              # the searched moment this clip shows, in the title
        extra = [w for w in topic.lower().split() if w not in game_words]
        if extra and all(w in t for w in extra):
            s += 3
    s += 1.5 if 35 <= len(title) <= 70 else -2 if len(title) > 90 else 0
    if title.isupper() or title.count("!") > 1:
        s -= 2                                             # shouting reads as spam
    return round(s, 1)


def pick_title(options, creator="", topic=""):
    options = [o.strip() for o in options if o and o.strip()]
    return max(options, key=lambda o: score_title(o, creator, topic)) if options else ""


def hashtags(tags, creator=""):
    """Always carry the streamer, the game and #shorts; cap at 5 (YouTube ignores all hashtags past 15 and 3-5 reads clean)."""
    seed = "#" + cfg()["auto"]["seo_seed"].replace(" ", "").lower()
    who = re.sub(r"[^a-z0-9]", "", short_name(creator).lower())
    lead = (["#" + who] if len(who) >= 3 else []) + [seed, "#shorts"]
    have = [h if h.startswith("#") else "#" + h for h in tags.split()]
    out = list(dict.fromkeys(lead + [h for h in have if h.lower() not in lead]))
    return " ".join(out[:5])


# ---- keyword research: which searches have demand, and how crowded they are ------------------------------

KEYWORDS = JOBS / "keywords.json"


def research(limit=None, log=print):
    """Expand the seed through YouTube autocomplete (seed + a..z), then measure each phrase on today's uploads:
    demand = views today's videos for it are pulling, competition = how many were uploaded today.
    Opportunity = median views per video today: high means viewers outnumber uploaders."""
    import statistics, string
    A = cfg()["auto"]
    seed, limit = A["seo_seed"], limit or A.get("research_limit", 40)
    rank, themed = {}, []
    themes = A.get("topic_seeds", [])
    for q in [seed] + [f"{seed} {c}" for c in string.ascii_lowercase] + [f"{seed} {t}" for t in themes]:
        got = [ph.lower().strip() for ph in suggestions(q)]
        for i, ph in enumerate(got):
            if ph != seed and seed in ph:
                rank[ph] = rank.get(ph, 0) + (10 - i)       # earlier in autocomplete = searched more
        if q.split(seed, 1)[-1].strip() in themes:           # keep the best few of every theme, however niche
            themed += [ph for ph in got if ph != seed and seed in ph][:3]
    picked = list(dict.fromkeys(sorted(rank, key=lambda k: -rank[k])[:limit] + themed))
    log(f"{len(rank)} search phrases found, measuring the top {len(picked)}")
    rows = []
    for ph in picked:
        try:
            vids = [v.get("view_count") or 0 for v in yt_search_today(ph, 20)]
        except Exception:
            continue
        vids = [v for v in vids if v]
        if vids:
            rows.append({"phrase": ph, "autocomplete": rank[ph], "uploads_today": len(vids), "median_views": int(statistics.median(vids)),
                         "top_views": max(vids)})
    rows.sort(key=lambda r: -r["median_views"])
    save_json(KEYWORDS, {"at": time.time(), "rows": rows})
    from . import topics
    topics.record(rows, log)
    return rows


def top_keywords(n=8):
    k = load_json(KEYWORDS)
    return [r["phrase"] for r in k["rows"][:n]] if k and time.time() - k["at"] < 36 * 3600 else []
