---
name: stream-clipper
description: Run a short-form clips channel from game streams with the stream-clipper project. Use when the user wants to find the best moments in a Twitch or YouTube stream or VOD, cut and edit vertical clips (hook, captions, narrator), research what people search for a game, post Shorts to YouTube, or run the whole thing hands-off. Triggers include "clip this stream", "find moments in this VOD", "make shorts from", "set up a clips channel", "what are people searching for <game>", "start the clipper".
---

# stream-clipper

A local pipeline: discover → score → cut → judge → edit → post → learn. You drive it through `python -m clipper <command>` inside the project folder.

## Before anything else

1. Find the project. If the current folder has `clipper/` and `config.toml`, use it. Otherwise ask whether to clone `https://github.com/taarruunnnn/stream-clipper`.
2. Check the environment once per session:
   - `python -m clipper --help` works (if not: create a virtual environment and `pip install -r requirements.txt`)
   - `ffmpeg -version`
   - for the animated style: `motion/node_modules` exists (if not: `cd motion && npm install`)
3. Read `config.toml` and, if present, `config.local.toml`. The user's own values belong in `config.local.toml`.

## What to run for what

| The user wants | Run |
|---|---|
| To see who is streaming the game | `python -m clipper streams` |
| What viewers are clipping on a channel | `python -m clipper radar <twitch-login>` |
| The best moments of a finished stream or video | `python -m clipper find <url>` |
| To clip a live stream as it happens | `python -m clipper live <url> --minutes N` |
| To score a candidate and write its hook, title, caption | `python -m clipper judge <clip-id>` |
| The finished vertical clip | `python -m clipper render <clip-id>` |
| To review, edit and post in a browser | `python -m clipper ui` |
| What people search for the game | `python -m clipper keywords`, then `python -m clipper topics` |
| Numbers on posted clips | `python -m clipper learn` |
| Everything, unattended | `python -m clipper auto` (macOS: `python -m clipper autostart on`) |

Clip ids look like `<job-folder>/<number>`; `python -m clipper list` shows them with their status.

## A first session, end to end

1. Ask for the game and the channel name. Write `[auto] game`, `[auto] seo_seed` and `[brand] handle` into `config.local.toml`.
2. `python -m clipper keywords` and show the top phrases.
3. `python -m clipper auto --once --no-live` to produce candidates, judge them and render the keepers.
4. `python -m clipper list`, then show the kept clips: hook, title, judge score and reason. Open the dashboard if the user wants to watch them.
5. Only if the user wants to post: walk them through the YouTube setup in the README, then `python -m clipper youtube-login`.

## Rules you must follow

- **Never post publicly without the user saying so.** The defaults are `mode = "approve"` and `privacy = "private"`. Do not change either to `auto` or `public`, and do not run `publish`, unless the user asks for that in this conversation. Do one private upload first and let them look at it.
- **Never commit or print secrets.** `client_secret*.json`, `.youtube_token.json` and `.env` stay local. Do not paste their contents into chat.
- **Sign-ins and consent screens are the user's to click.** Open the page; do not approve account access for them.
- **Be straight about permission.** Clipping other creators' streams needs their consent (a campaign, a clip policy, or asking). Editing a clip is not permission. If the user has not thought about this, say it once, plainly, then respect their decision.
- **Honour takedowns.** If a creator objects, remove the clip and add them to `[auto] blocklist`.
- **Report what actually happened.** Say which clips were rejected and why, which steps failed, and what you could not verify (you cannot hear audio; ask the user to listen).

## Things that commonly go wrong

- *"the loop is already running"*: only one loop may run. Check `python -m clipper autostart status`.
- *"signed in to a different channel"*: the login picked another channel than `[youtube] channel_id`. Run `youtube-login` again and choose the right one.
- A YouTube download crawls: section downloads of very long videos are throttled. Prefer Twitch clips or shorter VODs.
- Most candidates rejected: normal. The judge keeps roughly one in five to eight. Lower `[auto] min_score` only if the user wants more volume and accepts weaker clips.
- Motion renders are slow: they cost about ten times the classic style. The loop only uses motion for the clip being posted.
