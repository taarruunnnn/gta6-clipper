"""One call, every platform.

YouTube goes direct through your own Google API project (free, see youtube.py). Other platforms go through
Upload-Post if UPLOAD_POST_API_KEY is set (optional, paid for TikTok).

Every publish also copies the clip + caption into the outbox folder, so manual posting always works.
Secrets: put UPLOAD_POST_API_KEY (and optionally UPLOAD_POST_USER) in .env
"""
import json, shutil, time, urllib.error, urllib.request, uuid
from pathlib import Path
from . import youtube
from .util import ROOT, cfg, clip_dir, env, load_clip, save_clip

API = "https://api.upload-post.com/api"


def post_text(clip):
    parts = [clip.get("caption", "").strip()]
    if cfg()["brand"].get("credit") and clip.get("creator"):
        parts.append(f"🎥 {clip['creator']}")
    parts.append(clip.get("hashtags", "").strip())
    P = cfg()["publish"]
    if P.get("tool_credit", True):   # a one-line thank-you to the project; set tool_credit = false to leave it out
        parts.append(P.get("tool_credit_text", "Clipped with stream-clipper (free, open source): github.com/taarruunnnn/stream-clipper"))
    return "\n\n".join(p for p in parts if p)


def api(method, path, fields=None, file=None, timeout=600):
    key = env("UPLOAD_POST_API_KEY")
    if not key:
        raise RuntimeError("no UPLOAD_POST_API_KEY in .env")
    headers, data = {"Authorization": f"Apikey {key}"}, None
    if fields is not None:
        b = uuid.uuid4().hex
        body = bytearray()
        for k, v in fields:
            body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        if file:
            body += (f'--{b}\r\nContent-Disposition: form-data; name="video"; filename="{Path(file).name}"\r\n'
                     "Content-Type: video/mp4\r\n\r\n").encode() + Path(file).read_bytes() + b"\r\n"
        body += f"--{b}--\r\n".encode()
        data, headers["Content-Type"] = bytes(body), f"multipart/form-data; boundary={b}"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read())
        except Exception:
            j = {}
        raise RuntimeError(f"Upload-Post {e.code}: {j.get('message') or j.get('error') or e.reason}")


def uploadpost_accounts():
    if not env("UPLOAD_POST_API_KEY"):
        return {"user": "", "platforms": []}
    profiles = api("GET", "/uploadposts/users", timeout=20).get("profiles", [])
    want = env("UPLOAD_POST_USER")
    prof = next((p for p in profiles if p.get("username") == want), profiles[0] if profiles else None)
    if not prof:
        raise RuntimeError("no Upload-Post profile yet: create one and connect your accounts")
    return {"user": prof["username"], "platforms": [k for k, v in (prof.get("social_accounts") or {}).items() if v]}


def accounts():
    """Which platforms can be posted to right now, for the dashboard."""
    out = {"configured": False, "platforms": [], "user": "", "direct": []}
    if youtube.ready():
        cid, title = youtube.channel()
        want = cfg().get("youtube", {}).get("channel_id")
        if want and cid != want:
            out["error"] = "YouTube login is for a different channel: run `python -m clipper youtube-login` and pick the right one"
        else:
            out.update(configured=True, direct=["youtube"], platforms=["youtube"], user=title or "your YouTube channel")
    try:
        up = uploadpost_accounts()
        if up["platforms"]:
            out["configured"] = True
            out["platforms"] += [p for p in up["platforms"] if p not in out["platforms"]]
            out["user"] = out["user"] or up["user"]
        out["up_user"] = up["user"]
    except Exception as e:
        out["error"] = str(e)
    return out


def video_tags(clip):
    """Search tags: the game, the streamer, the searched topic, today's top phrases. YouTube allows 500 characters."""
    from . import seo
    A = cfg().get("auto", {})
    who = clip.get("creator", "")
    want = [A.get("seo_seed", ""), A.get("game", ""), who, f"{who} {A.get('seo_seed', '')}".strip(), clip.get("topic", "")]
    want += [h.lstrip("#") for h in clip.get("hashtags", "").split()] + seo.top_keywords(6) + ["shorts", "gaming clips"]
    out, size = [], 0
    for t in dict.fromkeys(x.strip() for x in want if x and x.strip()):
        if size + len(t) + 3 > 450:
            break
        out.append(t); size += len(t) + 3
    return out


def to_outbox(clip):
    out = ROOT / cfg()["publish"]["outbox"]
    out.mkdir(exist_ok=True)
    name = clip["id"].replace("/", "_")
    shutil.copy2(clip_dir(clip["id"]) / "out.mp4", out / f"{name}.mp4")
    (out / f"{name}.txt").write_text(f"{clip.get('title') or clip.get('hook', '')}\n\n{post_text(clip)}\n", encoding="utf-8")
    return out / f"{name}.mp4"


def record(clip_id, platform, ok, url="", error=""):
    clip = load_clip(clip_id)
    clip.setdefault("posts", {})[platform] = {"ok": ok, "url": url, "error": error, "at": time.time()}
    if ok:
        clip["status"] = "published"
    save_clip(clip)


def publish(clip_id, platforms=None, log=print):
    clip = load_clip(clip_id)
    video = clip_dir(clip_id) / "out.mp4"
    if not video.exists():
        raise RuntimeError("render the clip first")
    log(f"copied to {to_outbox(clip)}")
    acct = accounts()
    if not acct["configured"]:
        raise RuntimeError("clip is in the outbox folder. Connect YouTube (`python -m clipper youtube-login`) to post directly")
    platforms = platforms or cfg()["publish"]["platforms"]
    done = {p for p, v in clip.get("posts", {}).items() if v.get("ok")}
    todo = [p for p in platforms if p in acct["platforms"] and p not in done]   # never double-post
    skipped = [p for p in platforms if p not in acct["platforms"]]
    if skipped:
        log("not connected, skipped: " + ", ".join(skipped))
    if not todo:
        return "nothing to post (already posted, or no connected platform selected)"
    text = post_text(clip)
    title = (clip.get("title") or clip.get("hook") or clip.get("source_title", ""))[:100]
    if "youtube" in todo and "youtube" in acct["direct"]:
        log("uploading to YouTube")
        try:
            from . import motion
            A = cfg().get("avatar", {})
            r = youtube.upload(video, title, text, video_tags(clip), synthetic=bool(cfg()["brand"].get("motion") and motion.avatar_ready() and A.get("disclose", True)))
            record(clip_id, "youtube", True, r["url"])
            log(f"YouTube: {r['url']} ({r['privacy']})")
        except Exception as e:
            record(clip_id, "youtube", False, error=str(e))
            log(f"YouTube FAILED: {e}")
        todo.remove("youtube")
    if todo:
        via_uploadpost(clip_id, video, title, text, todo, acct["up_user"], log)
    posts = load_clip(clip_id).get("posts", {})
    return ", ".join(f"{p}: {'ok ' + v['url'] if v['ok'] else 'FAILED ' + v['error']}" for p, v in posts.items())


def via_uploadpost(clip_id, video, title, text, todo, user, log):
    fields = [("user", user), ("title", title), ("description", text), ("async_upload", "true"),
              ("tiktok_title", text[:2200]), ("instagram_title", text[:2200]), ("x_title", text[:280]),
              ("threads_title", text[:500]), ("facebook_title", text)] + [("platform[]", p) for p in todo]
    log(f"uploading to {', '.join(todo)}")
    r = api("POST", "/upload", fields, video)
    rid, results, t0 = r.get("request_id"), r.get("results"), time.time()
    while rid and time.time() - t0 < 900:
        time.sleep(6)
        s = api("GET", f"/uploadposts/status?request_id={rid}", timeout=30)
        if s.get("status") in ("completed", "failed"):
            results = s.get("results") or s
            break
    items = results.items() if isinstance(results, dict) else [(x.get("platform"), x) for x in (results or []) if isinstance(x, dict)]
    seen = set()
    for p, v in items:
        if p in todo and isinstance(v, dict):
            seen.add(p)
            record(clip_id, p, bool(v.get("success")), v.get("url") or v.get("post_url") or "", v.get("error") or v.get("message") or "")
    for p in set(todo) - seen:
        record(clip_id, p, False, error=f"no result yet, check upload-post.com (request {rid})")
