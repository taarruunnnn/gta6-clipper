"""The topic database: what people search for about the game, and which of those we have clips for.

Every keyword research run adds a demand reading per phrase, so the file becomes a history of what was
hot when. Phrases are sorted once (by Claude) into "moment" topics a 20-second clip can actually show
(a mission, a character, an ending, a glitch) and "generic" ones (live, gameplay, review) that only steer
wording. The judge tags each clip with the moment topic it shows; the loop goes looking for footage of
searched topics nobody has clipped for us yet.  File: data/topics.json
"""
import time
from .util import ROOT, all_clips, llm_json, load_json, save_json

DB = ROOT / "data" / "topics.json"


def load():
    return load_json(DB, None) or {"topics": {}}


def classify(phrases):
    """One Claude call for the phrases we have not seen before."""
    if not phrases:
        return {}
    prompt = ("These are YouTube search phrases about a video game. For each, answer whether a 20-second gameplay clip "
              "could SHOW it: \"moment\" for a specific mission, character, ending, glitch, vehicle, location, cheat or "
              "easter egg; \"generic\" for broad or navigational searches (live, gameplay, review, trailer, price, "
              "platform, a language, a creator's name). Reply with ONLY a JSON object mapping each phrase to "
              "\"moment\" or \"generic\".\n\n" + "\n".join(phrases))
    try:
        return llm_json(prompt, model="haiku", timeout=180)
    except Exception:
        return {}


def record(rows, log=print):
    """Add today's demand readings and classify new phrases."""
    db = load()
    new = [r["phrase"] for r in rows if r["phrase"] not in db["topics"]]
    kinds = classify(new)
    for r in rows:
        t = db["topics"].setdefault(r["phrase"], {"kind": kinds.get(r["phrase"], "generic"), "first_seen": time.strftime("%Y-%m-%d"),
                                                  "history": [], "clips": []})
        t["history"] = (t["history"] + [{"at": time.strftime("%Y-%m-%d %H:%M"), "median_views": r["median_views"],
                                         "top_views": r["top_views"], "autocomplete": r["autocomplete"]}])[-60:]
    save_json(DB, db)
    log(f"topic database: {len(db['topics'])} phrases, {sum(1 for t in db['topics'].values() if t['kind'] == 'moment')} are clip-able moments")
    return db


def demand(t):
    return t["history"][-1]["median_views"] if t["history"] else 0


def moments(only_open=False):
    """Clip-able topics, most searched first."""
    db = load()
    rows = [(p, t) for p, t in db["topics"].items() if t["kind"] == "moment" and (not only_open or not t["clips"])]
    return sorted(rows, key=lambda x: -demand(x[1]))


def tag(clip_id, phrase):
    db = load()
    t = db["topics"].get(phrase)
    if t is not None and clip_id not in t["clips"]:
        t["clips"].append(clip_id)
        save_json(DB, db)


def table():
    from .learn import views
    by_id = {c["id"]: c for c in all_clips()}
    out = [f"{'topic':<34}{'demand':>12}{'clips':>7}{'posted':>8}{'our views':>11}   first seen"]
    for p, t in moments():
        cs = [by_id[i] for i in t["clips"] if i in by_id]
        posted = [c for c in cs if c["status"] == "published"]
        out.append(f"{p:<34}{demand(t):>12,}{len(cs):>7}{len(posted):>8}{sum(views(c) for c in posted):>11,}   {t['first_seen']}")
    return "\n".join(out) if len(out) > 1 else "No topics yet: run `python -m clipper keywords` first."
