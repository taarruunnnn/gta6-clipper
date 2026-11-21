"""Drafts the hook, title and caption for a clip with Claude (uses the Claude Code CLI you already have,
so no API key is needed). Always a draft: edit it in the dashboard before rendering."""
import tempfile
from .learn import hook_examples
from .util import cfg, llm_json, load_clip, save_clip

PROMPT = """You write on-screen hooks and captions for short vertical gaming clips (TikTok, Reels, Shorts).

Clip transcript (what the streamer says, {secs:.0f} seconds):
\"\"\"{text}\"\"\"
Source video title: {title}
Streamer: {creator}
Why it was picked: {why}
{learned}
Write:
- "hooks": 3 different on-screen hooks, max 7 words each. Each must make a viewer need to see the payoff
  without giving it away, and must be literally true to the transcript. No clickbait that the clip does
  not deliver, no emojis, no hashtags.
- "title": a YouTube Shorts title, max 70 characters.
- "caption": 1-2 short sentences for the post, ending with a question that invites comments.
- "hashtags": 4-6 hashtags as one string, mixing broad and specific.

Reply with ONLY a JSON object with those four keys."""


def draft(clip_id, log=print):
    clip = load_clip(clip_id)
    prompt = PROMPT.format(secs=clip["out"] - clip["in"], text=clip["text"][:1500], title=clip.get("source_title", ""),
                           creator=clip.get("creator", ""), why=", ".join(clip.get("why", [])), learned=hook_examples())
    log(f"drafting copy for {clip_id}")
    with tempfile.TemporaryDirectory() as tmp:   # neutral folder so no project context is loaded
        j = llm_json(prompt, cwd=tmp)
    clip = load_clip(clip_id)
    hooks = [h.strip() for h in j.get("hooks", []) if h.strip()]
    clip["hook_options"] = hooks
    if hooks and not clip.get("hook"):
        clip["hook"] = hooks[0]
    clip["title"] = clip.get("title") or j.get("title", "")
    clip["caption"] = clip.get("caption") or j.get("caption", "")
    if j.get("hashtags") and clip.get("hashtags", "") == cfg()["publish"]["default_hashtags"]:
        clip["hashtags"] = j["hashtags"]
    save_clip(clip)
    return hooks
