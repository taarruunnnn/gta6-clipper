"""Watch a live stream and cut a candidate the moment chat explodes.

The stream is copied to disk as-is (no re-encode, ~1% CPU) into a rolling buffer; old video is deleted, so
disk use stays flat however long the stream runs. Twitch chat is read anonymously over IRC, YouTube chat
through yt-dlp. Each spike becomes a normal candidate in the dashboard.
"""
import json, re, socket, subprocess, sys, threading, time
from collections import Counter
from . import sources
from .moments import make_candidate
from .util import cfg, job_dir, next_clip_number, run, save_json, ytdlp

HYPE = sources.CHAT_HYPE
NOISE = re.compile(r"(?i)giveaway|\braid|!\w+|https?://|discord\.gg|promo code|subscribe")


class Chat:
    """Per-second message counts (hype messages count double)."""

    def __init__(self):
        self.n, self.w, self.lock = Counter(), Counter(), threading.Lock()

    def add(self, ts, text):
        if text and not NOISE.search(text):
            with self.lock:
                self.n[int(ts)] += 1
                self.w[int(ts)] += 2 if HYPE.search(text) else 1

    def total(self, c, a, b):
        with self.lock:
            return sum(c.get(s, 0) for s in range(a, b + 1))


def twitch_chat(login, chat, stop):
    while not stop.is_set():
        try:
            s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30)
            s.sendall(f"NICK justinfan{int(time.time()) % 90000 + 10000}\r\nJOIN #{login}\r\n".encode())
            buf = b""
            while not stop.is_set():
                data = s.recv(4096)
                if not data:
                    break
                *lines, buf = (buf + data).split(b"\r\n")
                for ln in lines:
                    t = ln.decode("utf-8", "ignore")
                    if t.startswith("PING"):
                        s.sendall(b"PONG :tmi.twitch.tv\r\n")
                    elif " PRIVMSG #" in t:
                        chat.add(time.time(), t.split(" :", 1)[-1])
        except OSError:
            time.sleep(3)


def youtube_chat(url, d, chat, stop):
    p = subprocess.Popen([sys.executable, "-m", "yt_dlp", "--no-warnings", "--skip-download", "--write-subs", "--sub-langs",
                          "live_chat", "-o", str(d / "livechat"), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pos, t0, seen = {}, time.time(), set()
    while not stop.is_set():
        time.sleep(1)
        for f in d.glob("livechat*.json*"):
            with open(f, "rb") as fh:
                fh.seek(pos.get(f.name, 0))
                data = fh.read()
            cut = data.rfind(b"\n") + 1
            pos[f.name] = pos.get(f.name, 0) + cut
            for ln in data[:cut].splitlines():
                try:
                    for a in json.loads(ln)["replayChatItemAction"]["actions"]:
                        r = a["addChatItemAction"]["item"]["liveChatTextMessageRenderer"]
                        ts = int(r["timestampUsec"]) / 1e6
                        if r["id"] not in seen and ts > t0 - 5:
                            seen.add(r["id"])
                            chat.add(ts, "".join(x.get("text", "") for x in r["message"]["runs"]))
                except Exception:
                    continue
    p.terminate()


class Recorder:
    """ffmpeg stream copy into 20 s segments; each finished segment's mtime is its wall-clock end."""

    def __init__(self, url, d, minutes):
        self.d = d
        h = cfg()["live"]["height"]
        urls = [u for u in ytdlp("-g", "-f", f"b[height<={h}]/bv*[height<={h}]+ba/b", url, capture=True).stdout.split() if u.startswith("http")]
        if not urls:
            raise RuntimeError("could not open the stream (is it live?)")
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for u in urls:
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", u]
        for i in range(len(urls)):
            cmd += ["-map", str(i)]
        cmd += ["-c", "copy", "-t", str(int(minutes * 60)), "-f", "segment", "-segment_time", "20", "-segment_format", "mpegts",
                "-reset_timestamps", "1", "-segment_list", str(d / "segs.csv"), "-segment_list_type", "csv", str(d / "seg_%05d.ts")]
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def segments(self):
        """[(path, wall_start, wall_end)] for finished segments."""
        out, csv = [], self.d / "segs.csv"
        for ln in (csv.read_text().splitlines() if csv.exists() else []):
            q = ln.split(",")
            f = self.d / q[0]
            if len(q) >= 3 and f.exists():
                end = f.stat().st_mtime
                out.append((f, end - (float(q[2]) - float(q[1])), end))
        return out

    def prune(self, keep_seconds):
        for f, _, end in self.segments():
            if end < time.time() - keep_seconds:
                f.unlink(missing_ok=True)

    def cut(self, a, b, out):
        """Join the segments covering wall-clock [a, b]; returns the wall time of the file's first second."""
        segs = [s for s in self.segments() if s[2] > a and s[1] < b]
        if not segs:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.d / "join.txt"   # next to the segments, so the names resolve
        tmp.write_text("".join(f"file '{s[0].name}'\n" for s in segs))
        r = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", tmp, "-c", "copy",
                 "-movflags", "+faststart", out], check=False, capture=True)
        return segs[0][1] if r.returncode == 0 and out.exists() else None

    def stop(self):
        if self.p.poll() is None:
            try:
                self.p.stdin.write(b"q"); self.p.stdin.flush(); self.p.wait(15)
            except Exception:
                self.p.kill()


def watch(url, minutes=60, log=print):
    L = cfg()["live"]
    d = job_dir(url)
    ld = d / "live"
    ld.mkdir(exist_ok=True)
    for f in ld.glob("*"):
        f.unlink()
    meta = sources.info(url)
    save_json(d / "info.json", meta)
    chat, stop = Chat(), threading.Event()
    m = re.match(r"https?://(?:www\.)?twitch\.tv/(\w+)/?$", url)
    target, args = (twitch_chat, (m.group(1).lower(), chat, stop)) if m else (youtube_chat, (url, ld, chat, stop))
    threading.Thread(target=target, args=args, daemon=True).start()
    rec = Recorder(url, ld, minutes)
    log(f"watching {meta.get('uploader') or url} for {minutes:g} min (Ctrl+C to stop)")
    start, last, pending, made = time.time(), 0.0, [], 0
    win = int(L["window"])

    def finish(spike, rate):
        nonlocal made
        a, b = spike - L["before"] - 8, spike + L["after"] + 8
        cd = d / "clips" / str(next_clip_number(d))
        first = rec.cut(a, b, cd / "src.mp4")
        if first is None:
            return log("  spike missed: recording not ready")
        try:
            make_candidate(url, d, meta, spike - first, [f"chat spike {rate} msgs/{win}s"], float(rate), log,
                           section=cd / "src.mp4", sec_start=0.0)
            made += 1
        except Exception as e:
            log(f"  could not build candidate: {e}")

    try:
        while rec.p.poll() is None and time.time() < start + minutes * 60 + 20:
            time.sleep(1)
            t = time.time()
            e = int(t) - 2
            if e - start > win + 30 and t - last > L["cooldown"]:
                raw = chat.total(chat.n, e - win + 1, e)
                rate = chat.total(chat.w, e - win + 1, e) / win
                span = int(min(300, e - start - win))
                base = chat.total(chat.w, e - win - span + 1, e - win) / span
                if raw >= L["min_messages"] and rate >= L["ratio"] * max(base, 0.05):
                    last = t
                    spike = e - win / 2 - 4   # the moment itself: mid-window minus viewer reaction time
                    log(f"SPIKE: {raw} msgs/{win}s vs usual {base * win:.1f}")
                    pending.append((spike, raw, t + L["after"] + 30))   # wait for the segment to close
            for p in [p for p in pending if p[2] <= t]:
                pending.remove(p); finish(p[0], p[1])
            rec.prune(L["buffer_minutes"] * 60)
    except KeyboardInterrupt:
        log("stopping")
    finally:
        rec.stop(); stop.set()
        time.sleep(1)
        for p in pending:
            finish(p[0], p[1])
        for f in ld.glob("seg_*.ts"):
            f.unlink()
    log(f"done: {made} candidate(s). Review them with: python -m clipper ui")
