"""Local dashboard: review candidates, edit the hook, render, and post everywhere with one click.

Listens on 127.0.0.1 only. Heavy jobs go through ONE worker, so the laptop never runs two renders at once.
"""
import json, mimetypes, queue, subprocess, threading, time, traceback, webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from . import publish as pub
from .util import JOBS, ROOT, all_clips, be_nice, cfg, clip_dir, load_clip, save_clip

LOG, Q, STATE = deque(maxlen=200), queue.Queue(), {"busy": "", "queued": []}
EDITABLE = ("hook", "title", "caption", "hashtags", "layout", "in", "out")


def log(msg):
    LOG.append(f"{time.strftime('%H:%M:%S')}  {msg}")


def enqueue(label, fn, *args):
    STATE["queued"].append(label)
    Q.put((label, fn, args))


def worker():
    be_nice()
    while True:
        label, fn, args = Q.get()
        STATE["queued"].remove(label)
        STATE["busy"] = label
        try:
            r = fn(*args, log=log)
            if isinstance(r, str):
                log(r)
        except Exception as e:
            log(f"ERROR in {label}: {e}")
            traceback.print_exc()
        STATE["busy"] = ""


def do_find(src, log):
    from .moments import find
    clips = find(src, log=log)
    for c in clips:   # draft copy + render straight away so candidates arrive watchable
        do_copy(c["id"], log)
        do_render(c["id"], log)


def do_copy(cid, log):
    from .copywriter import draft
    try:
        draft(cid, log)
    except Exception as e:
        log(f"copy not drafted ({e})")


def do_render(cid, log):
    from .render import render
    render(cid, log)


def do_post(cid, platforms, log):
    return pub.publish(cid, platforms, log)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path):
        """Static file with Range support (Safari will not play video without it)."""
        if not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        size = path.stat().st_size
        a, b = 0, size - 1
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            x, _, y = rng[6:].partition("-")
            a = int(x) if x else max(0, size - int(y))
            b = min(int(y), size - 1) if x and y else b
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(b - a + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                left -= len(chunk)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/":
            return self.send_file(ROOT / "web" / "index.html")
        if p == "/api/state":
            return self.send_json({"clips": all_clips(), "busy": STATE["busy"], "queued": STATE["queued"], "log": list(LOG)[-40:],
                                   "platforms": cfg()["publish"]["platforms"], "layouts": ["focus", "fill", "stack"]})
        if p == "/api/accounts":
            return self.send_json(pub.accounts())
        if p.startswith("/media/"):
            try:
                job, n, name = p[7:].split("/")
                d = clip_dir(f"{job}/{n}")
            except ValueError:
                return self.send_json({"error": "bad path"}, 400)
            if name not in ("out.mp4", "thumb.jpg"):
                return self.send_json({"error": "bad path"}, 400)
            return self.send_file(d / name)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        # only this page may call the API: blocks other websites from triggering posts in your browser
        origin, host = self.headers.get("Origin", ""), self.headers.get("Host", "")
        if (origin and urlparse(origin).netloc != host) or self.headers.get("Content-Type") != "application/json":
            return self.send_json({"error": "forbidden"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            p = urlparse(self.path).path
            cid = body.get("id", "")
            if p == "/api/find":
                enqueue(f"find {body['src'][:60]}", do_find, body["src"].strip())
            elif p == "/api/save":
                c = load_clip(cid)
                for k in EDITABLE:
                    if k in body:
                        c[k] = float(body[k]) if k in ("in", "out") else str(body[k])
                save_clip(c)
            elif p == "/api/render":
                enqueue(f"render {cid}", do_render, cid)
            elif p == "/api/copy":
                enqueue(f"copy {cid}", do_copy, cid)
            elif p == "/api/post":
                load_clip(cid)
                enqueue(f"post {cid}", do_post, cid, body.get("platforms") or None)
            elif p == "/api/learn":
                from .learn import refresh
                enqueue("learn", lambda log: refresh(log))
            elif p == "/api/reveal":
                f = clip_dir(cid) / "out.mp4"
                subprocess.run(["open", "-R", str(f if f.exists() else clip_dir(cid))])
            elif p == "/api/discard":
                c = load_clip(cid); c["status"] = "discarded"; save_clip(c)
            else:
                return self.send_json({"error": "not found"}, 404)
            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"error": str(e)}, 400)


def serve(port=8765, open_browser=True):
    JOBS.mkdir(exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"dashboard: {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
