# Contributing

Thanks for looking. This started as one person's tool, so there is a lot of room to help.

## Most useful right now

- **Run it on Linux or Windows and report what breaks.** Those code paths are written but untested: transcription with faster-whisper, NVENC or x264 encoding, fonts, starting at boot.
- **Tests.** There are none. Scoring (`moments.py`), cut snapping (`transcribe.refine`), title scoring (`seo.score_title`) and the caption timeline are all pure functions that are easy to test.
- **More posting targets** behind the same `publish` interface.
- **Better moment signals** for streams where loudness is the only one.
- **Live mode on Twitch** records ad-break screens for anonymous viewers; detecting and skipping them would save a lot of wasted work.

## How to send a change

1. Open an issue first for anything larger than a small fix, so we agree on the approach.
2. Keep pull requests focused on one thing.
3. Say what you ran it on (OS, GPU) and what you checked by hand.
4. Never include footage, tokens, `client_secret*.json` or your `config.local.toml`.

## Ground rules

- The defaults stay cautious: approval before posting, private uploads.
- No feature that posts, signs in or accepts terms without the user doing something explicit.
- Be decent to each other and to the creators whose streams this tool touches.
