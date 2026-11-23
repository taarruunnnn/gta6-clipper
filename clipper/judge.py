"""The stand-in for your own eyes: Claude looks at three frames and the transcript of each candidate,
scores it as a standalone short, and writes the hook, title, caption and commentary line in the same call."""
import re
from . import seo, topics
from .learn import hook_examples
from .util import all_clips, cfg, clip_dir, llm_json, load_clip, load_json, run, save_clip

PROMPT = """You are the editor of a short-form gaming clips page (YouTube Shorts). Decide whether this candidate
clip is worth posting, and if so write its copy.

First read the images j1.jpg, j2.jpg and j3.jpg in the current folder: frames from the start, peak and end.

Transcript ({secs:.0f} seconds): \"\"\"{text}\"\"\"
Streamer: {creator}
Source title: {title}
Picked because: {why}
Speech: {speech}
{learned}
{recent}
{seo}
{topics}

{focus}
Score 1-10 as a standalone short for someone who has never seen the stream:
- 8-10: clear setup and payoff, something visibly happens, strong reaction, understandable with no context.
- 5-7: fine but ordinary.
- A clip where nobody speaks for most of its length needs strong visible action to score above 5.
- 1-4: no payoff, mostly silence or idle chatter, menus/loading/"be right back" screens, or needs context.
The page posts in these languages only: {langs}. If the speech is in another language, score it 1.
The page posts {game} only. If the frames or the source title show a different game (an older game in the same
series counts as different), score it 1.
Set "safe" to false (and it will never be posted) if it contains slurs, hate, sexual content, harassment of a
real person, someone's private information, or anything that would embarrass the page.

Reply with ONLY a JSON object:
{{"score": int, "reason": "one sentence", "safe": bool,
  "hooks": [3 on-screen hooks, max 7 words each, true to the clip, no spoilers, each ending with ONE fitting emoji],
  "titles": [{title_rule}],
  "caption": "2-3 sentence description: first sentence says what happens using a real search phrase, last one is
              a question that invites comments",
  "hashtags": "4-6 hashtags in one string",
  "peak_text": "on-screen cue shown 2 seconds BEFORE the big moment, max 4 words plus one emoji, e.g. WATCH THE VAN 👀",
  "payoff_text": "on-screen reaction slammed in right AFTER the big moment, max 3 words plus one emoji, e.g. HE'S COOKED 💀",
  "ask": "{ask_rule}",
  "emojis": {{up to 4 entries mapping a single WORD that is spoken in the transcript to the emoji that should pop up when it is said; never weapon, blood or violent emojis}},
  "outro": "the narrator's spoken reaction after the payoff, max 7 words, cheeky, same British gamer voice",
  "topic": the one phrase from SEARCHED TOPICS that this clip genuinely shows, copied exactly, or null if none fits,
  "commentary": "one spoken line, max 14 words, in the voice of a hyped British gaming YouTuber talking to mates: casual,
                 a little cheeky, natural slang, sets up what is about to happen without spoiling it. If a topic fits, the
                 titles and caption must use that topic phrase."}}"""


FOCUS = """This clip was picked to answer a search: people are typing "{topic}" into YouTube right now.
For this clip the question is whether someone who searched that would be satisfied, not how loud the reaction is:
- 8-10: the frames and speech clearly show or explain "{topic}", a viewer learns or sees the thing itself, and it
  makes sense with no context. A calm clip can score here.
- 5-7: it is about the topic but only shows part of it, or it takes too long to get there.
- 1-4: the topic is only mentioned in passing, the screen is dark or a menu, or it needs the rest of the video.
The silence and safety rules below still apply. If it does show the topic, set "topic" to exactly "{topic}"."""


ASK = """on-screen line for the last 3 seconds that gives the viewer a reason to comment, share or like instead of just
          swiping on. Max 7 words plus one emoji, about THIS clip. Use the form that fits: a question with two sides
          people will argue about (Was that his fault or the NPC's? 👇), a dare to send it to someone (Send this to your
          getaway driver 😭), or a like-if (Like if you'd have jumped too 👍). Never a generic like-and-subscribe line"""

TITLE_RULES = {
    "name": """3 different YouTube titles, 35-65 characters, written the way the big clip channels write them: ONE plain
             sentence that opens with the streamer's name as fans say it ("{who}", or their well-known short name), says the
             specific thing they do, find or say, and includes the game name. End with ONE fitting emoji. No colon, no
             keyword label in front ("GTA 6 Gameplay: ..."), no "Live", never the word "streamer".
             Shape: "CaseOh Gets Chased by a Gator in GTA 6 😭". Curiosity, no lies""",
    "search": """3 different YouTube titles, 40-70 characters, each containing the game name early and a phrase people
             are actually searching; curiosity, no lies. Do NOT put the streamer's name or nickname in the title:
             the credit is already on the video""",
}


def title_rule(creator):
    """How titles are written: [auto] title_style. Without a streamer name there is nobody to open with."""
    who = seo.short_name(creator)
    style = cfg()["auto"].get("title_style", "name") if who else "search"
    return TITLE_RULES.get(style, TITLE_RULES["name"]).replace("{who}", who)


def picked_topic(clip):
    """The searched topic a clip was cut for, if it came from a topic search."""
    m = re.search(r"topic (?:video|hunt) \(([^)]+)\)", " ".join(clip.get("why", [])))
    return m.group(1) if m else ""


def retitle(clip_id):
    """New title for an already-judged clip, in the current title style ([auto] title_style)."""
    clip = load_clip(clip_id)
    who = clip.get("creator", "")
    j = llm_json(f"""Write YouTube Shorts titles for this clip: {title_rule(who)}.
Keep what the current title promises and do not add anything the clip does not show.
{seo.brief("")}
Searched topic to keep in the title, if any: {clip.get("topic") or "none"}
Streamer: {who}
Current title: {clip.get("title")}
On-screen hook: {clip.get("hook")}
What happens in the clip: {(clip.get("judge") or {}).get("reason", "")}
Reply with ONLY a JSON object: {{"titles": ["...", "...", "..."]}}""", model="haiku", timeout=120)
    clip = load_clip(clip_id)
    clip["title"] = seo.pick_title(j.get("titles", []), who, clip.get("topic", "")) or clip["title"]
    save_clip(clip)
    return clip["title"]


def judge(clip_id, log=print):
    clip = load_clip(clip_id)
    d = clip_dir(clip_id)
    L = clip["out"] - clip["in"]
    for i, t in enumerate((clip["in"] + 0.12 * L, min(clip["out"] - 0.5, max(clip["in"] + 0.5, clip["peak"])), clip["out"] - 0.1 * L), 1):
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", d / "src.mp4", "-frames:v", "1", "-vf", "scale=640:-2", d / f"j{i}.jpg"], check=False)
    open_topics = [p for p, _ in topics.moments()[:25]]
    wanted = picked_topic(clip)
    if wanted and wanted not in open_topics:
        open_topics.append(wanted)
    topic_brief = "SEARCHED TOPICS (moments people are looking for right now): " + ("; ".join(open_topics) or "none yet")
    used = [c["hook"] for c in all_clips() if c.get("hook") and c["id"] != clip_id][:14]
    recent = ("Hooks this page used recently: " + "; ".join(f'"{h}"' for h in used) +
              ". Yours must NOT reuse their wording or sentence pattern. Vary the form: a question, a number, a "
              "plain statement, a quote from the clip.") if used else ""
    asked = [c["ask"] for c in all_clips() if c.get("ask") and c["id"] != clip_id][:6]
    if asked:
        recent += "\nClosing lines this page used recently: " + "; ".join(f'"{a}"' for a in asked) + ". Use a different form and wording."
    words = load_json(d / "words.json", [])
    said, gaps, prev = [w for w in words if clip["in"] <= w["s"] < clip["out"]], [], clip["in"]
    for w in said + [{"s": clip["out"], "e": clip["out"]}]:
        if w["s"] - prev >= 4:
            gaps.append(f"{prev - clip['in']:.0f}-{w['s'] - clip['in']:.0f}s")
        prev = w["e"]
    speech = f"{len(said)} words spoken" + (f"; nobody speaks during {', '.join(gaps)}" if gaps else "; speech throughout")
    prompt = PROMPT.format(focus=FOCUS.format(topic=wanted) if wanted else "", recent=recent, speech=speech, secs=L, text=clip["text"][:1500], creator=clip.get("creator", ""), title=clip.get("source_title", ""),
                           why=", ".join(clip.get("why", [])), learned=hook_examples(), title_rule=title_rule(clip.get("creator", "")), ask_rule=ASK,
                           seo=seo.brief(clip.get("creator", "")), topics=topic_brief,
                           langs=", ".join(cfg()["auto"]["languages"]) or "any", game=cfg()["auto"]["game"])
    j = llm_json(prompt, cwd=d)
    clip = load_clip(clip_id)
    music = bool(re.search(r"[♪♫]|\[music\]", clip["text"], re.I))   # music-heavy audio is what gets auto-claimed
    clip["judge"] = {"score": int(j.get("score", 0)), "reason": j.get("reason", ""), "safe": bool(j.get("safe", False)), "music": music}
    hooks = [h.strip() for h in j.get("hooks", []) if h.strip()]
    clip["hook_options"] = hooks
    clip["hook"] = clip.get("hook") or (hooks[0] if hooks else "")
    topic = j.get("topic") if j.get("topic") in open_topics else ""
    clip["topic"] = topic or ""
    clip["title_options"] = {t: seo.score_title(t, clip.get("creator", ""), topic) for t in j.get("titles", [])}
    clip["title"] = clip.get("title") or seo.pick_title(j.get("titles", []), clip.get("creator", ""), topic)
    for k in ("peak_text", "payoff_text", "outro", "ask"):
        clip[k] = (j.get(k) or "").strip()
    if len(clip["ask"].split()) > 9:
        clip["ask"] = ""                                   # too long to read in three seconds
    clip["emojis"] = {str(k).lower().strip(".,!?'\""): str(v) for k, v in (j.get("emojis") or {}).items()} if isinstance(j.get("emojis"), dict) else {}
    for k in ("caption", "commentary"):
        clip[k] = clip.get(k) or j.get(k, "")
    if clip.get("hashtags", "") == cfg()["publish"]["default_hashtags"]:
        clip["hashtags"] = seo.hashtags(j.get("hashtags") or clip["hashtags"], clip.get("creator", ""))
    ok = clip["judge"]["safe"] and not music and clip["judge"]["score"] >= cfg()["auto"]["min_score"]
    if not ok and clip["status"] == "candidate":
        clip["status"] = "rejected"
    save_clip(clip)
    if topic and ok:
        topics.tag(clip_id, topic)
    log(f"  judge {clip_id}: {clip['judge']['score']}/10 {'KEEP' if ok else 'reject'} - {clip['judge']['reason'][:90]}")
    return ok
