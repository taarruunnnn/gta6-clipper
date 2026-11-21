# Notes for coding agents (Codex, Claude Code, others)

gta6-clipper is a local Python pipeline that turns game streams into vertical Shorts. Read `README.md` for what it does and `skills/gta6-clipper/SKILL.md` for how to operate it on a user's behalf; the rules in that skill apply to you too.

## Run and check

- Everything runs through `python -m clipper <command>` from the repo root (see `clipper/__main__.py`).
- There is no test suite yet. After a change, at minimum: `python -c "import clipper.auto, clipper.render, clipper.judge, clipper.publish, clipper.server"` and run the command you touched on a short local video (`python -m clipper find some.mp4 --top 1`, then `render`).
- The animated layer lives in `motion/` (Remotion, React). `cd motion && npm install` once.

## Layout

- `clipper/moments.py` scoring and candidate creation; `sources.py` YouTube and Twitch access; `transcribe.py` speech to word timestamps
- `clipper/judge.py` clip scoring and copy; `seo.py` keyword research and title scoring; `topics.py` topic database
- `clipper/render.py` ffmpeg edit; `overlay.py` still text layer (Pillow); `motion.py` animated text layer (Remotion)
- `clipper/publish.py`, `youtube.py` posting; `learn.py` stats; `auto.py` the unattended loop; `live.py` live recording; `server.py` + `web/` dashboard

## Conventions

- Settings go in `config.toml` with a comment; a user's own values go in `config.local.toml`, which is ignored. Read settings through `util.cfg()`.
- Clip state is one JSON file per clip: `jobs/<job>/clips/<n>/clip.json`. Use `util.load_clip` / `util.save_clip`.
- All assistant calls go through `util.ask_llm` / `util.llm_json`, so the command stays swappable.
- Platform-specific code needs a fallback: macOS is the only tested platform. Say in the PR what you ran it on.
- Keep modules small and dependency-light; no new services or API keys unless optional.

## Do not

- Commit `client_secret*.json`, `.youtube_token.json`, `.env`, `config.local.toml`, `jobs/`, `data/` or `brand/`.
- Change the cautious defaults (`mode = "approve"`, `privacy = "private"`).
- Add code that posts, signs in, or accepts terms without an explicit user action.
