# stream-clipper

**A hands-off clip factory for game streams.** It finds the best moments in live streams and VODs, turns them into vertical Shorts with a hook, animated captions, a narrator and sound design, writes search-optimised titles, and posts them to YouTube. It runs on your own computer.

Built with [Claude Code](https://claude.com/claude-code) in a single long session, for a GTA 6 clips channel. It works for any game Twitch has a category for.

> If this is useful to you, a star helps other people find it. Issues and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## What it does

```
discover ──► score ──► cut ──► judge ──► edit ──► post ──► learn
```

| Stage | How |
|---|---|
| **Discover** | Today's most-viewed Twitch viewer clips for the game, the top live streams (watched for chat spikes), and the most-viewed finished YouTube streams |
| **Score** | Every second of a long stream is scored from audio only: loudness jumps, YouTube "most replayed", chat rate, viewer clips. Only the winning 40 seconds are downloaded as video |
| **Cut** | Speech is transcribed with word timestamps; the cut starts on a sentence about 12 seconds before the peak and ends on a finished one |
| **Judge** | An assistant looks at three frames and the transcript, scores the clip 1-10, and rejects anything unsafe, off-language or without a payoff |
| **Edit** | 1080x1920: gameplay over a blurred copy of itself, hook box, word-by-word captions, emojis, a "watch" cue before the peak, reaction text after it, zoom and flash on the peak, narrator intro and reaction, a sound on every text element |
| **Post** | Direct YouTube upload with your own Google API project (free, 100 uploads a day). Title picked by rule from live search data |
| **Learn** | View counts are pulled back in; your best and worst hooks and titles feed the next ones. A post that gets taken down blocks that creator |

There is also a keyword research command (what people search for the game, ranked by the views today's videos are pulling) and a topic database that tracks which searched moments you have clips for.

## Requirements

- Python 3.10 or newer, `ffmpeg`, and Node.js 18+ (only for the animated style)
- [Claude Code](https://claude.com/claude-code) signed in, for judging and copywriting (or another command-line assistant, see `[llm]` in `config.toml`)
- A Google account with a YouTube channel, if you want it to post

### Platform support

| | Status |
|---|---|
| macOS on Apple Silicon | Developed and tested here. GPU transcription (MLX), hardware encoding (VideoToolbox), start at login |
| Linux, Windows, NVIDIA GPUs | Code paths exist (faster-whisper, NVENC or x264, font fallbacks) but **have not been run**. Expect to fix things; reports and PRs are very welcome |

## Quick start

```bash
git clone https://github.com/taarruunnnn/stream-clipper && cd stream-clipper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
(cd motion && npm install)          # optional: the animated text style

python -m clipper ui                # dashboard at http://127.0.0.1:8765
```

Paste a stream or VOD link into the dashboard, or from the terminal:

```bash
python -m clipper streams                 # who is live with the game right now
python -m clipper radar <twitch-login>    # what viewers are clipping on a channel
python -m clipper find <video-or-VOD-url> # best moments of a finished video
python -m clipper live https://www.twitch.tv/<login> --minutes 120
python -m clipper keywords                # search phrases ranked by views per upload
python -m clipper topics                  # searched moments, and your clips for each
python -m clipper learn                   # view counts and what is working
python -m clipper auto                    # the whole loop, hands-off
python -m clipper autostart on            # macOS: start the loop at login
```

## Make it yours

`config.toml` holds the defaults. Put your own values in `config.local.toml` (same sections; it is git-ignored and wins over the defaults):

```toml
[brand]
handle = "@yourchannel"
accent = "#FFD400"

[auto]
game = "Grand Theft Auto VI"     # the Twitch category
seo_seed = "gta 6"               # the phrase titles are built around
mode = "approve"                 # or "auto" to post without asking

[youtube]
channel_id = "UC..."             # uploads to any other channel are refused
privacy = "private"              # switch to "public" after checking one upload
```

The defaults are deliberately cautious: clips wait for your approval and uploads are private.

### Posting to YouTube

1. In Google Cloud Console create a project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen (External) and add yourself as a test user.
3. Create an OAuth client of type **Desktop app**, download the JSON, save it as `client_secret.json` in this folder.
4. `python -m clipper youtube-login` and approve access to your channel.

While the app is in "Testing" the sign-in may need renewing about weekly.

### The two edit styles

| | Classic | Motion (`motion = true`) |
|---|---|---|
| Text | Still hook box and captions | Animated hook, popping captions, emojis, cue and reaction text |
| Audio | Narrator intro | Narrator intro and reaction, a sound on every text element |
| Cost on an M5, per 20 s clip | about 16 s, 34 CPU-seconds | about 100-160 s, 360-530 CPU-seconds |

The loop renders previews in the classic style and only renders motion for the clip it is about to post.

An optional reacting avatar tile is included (`[avatar]`, off by default): bring five square images of your own character (`idle`, `talk`, `watch`, `shock`, `laugh`).

## Use it from an AI assistant

**Claude Code** (plugin with a skill):

```
/plugin marketplace add taarruunnnn/stream-clipper
/plugin install stream-clipper@stream-clipper
```

Then ask: *"Use stream-clipper to set up a clips channel for <game> and find today's best moments."*

**Claude apps**: zip the `skills/stream-clipper` folder and upload it as a skill.

**ChatGPT / Codex**: the Codex CLI reads [`AGENTS.md`](AGENTS.md) and can run the project the same way. ChatGPT in the browser cannot run local tools, but you can paste `skills/stream-clipper/SKILL.md` into a project or custom GPT as instructions and it will guide you through the commands.

## Use it responsibly

This tool reposts other people's work. That puts obligations on you, not on the software.

- Clip creators who allow it: a clipping campaign, a stated clip policy, or direct permission. Keep the credit on.
- Adding a hook and captions is editing, not permission. A creator or publisher can still have a clip removed, and platforms act on claims first.
- If a creator asks you to take something down, do it and add them to `blocklist`.
- Plain reposts are generally not eligible for platform monetisation. Your own commentary is what changes that.
- Posting unattended means nobody looked at the clip. The judge filters a lot, not everything. Start in `approve` mode.
- Not affiliated with Rockstar Games, Take-Two, Twitch, YouTube or Anthropic.

## Known limits

- Streams longer than 4 hours are skipped on YouTube; chat replay is given up on after 3 minutes.
- Live mode on Twitch records what an anonymous viewer sees, including ad-break screens. The judge rejects those, but they waste time.
- Loudness alone is a weak signal; most picks from streams with no other signal get rejected.
- The narrator voice uses an unofficial free route to Microsoft's online voices (`edge-tts`); it can break without notice.
- No tests yet.

## Project layout

```
clipper/      the pipeline (moments, transcribe, judge, render, motion, publish, youtube, seo, topics, learn, auto, live)
motion/       Remotion project that renders the transparent animated text layer
web/          the local dashboard
skills/       the assistant skill
config.toml   defaults      config.local.toml   your overrides (ignored)
```

## License

MIT. See [LICENSE](LICENSE).
