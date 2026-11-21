"""Direct YouTube upload with your own Google API project. Free: 100 uploads a day.

One-time setup (you do this, it takes about 5 minutes):
  1. console.cloud.google.com: create a project, enable "YouTube Data API v3".
  2. OAuth consent screen: External, add yourself, then set publishing status to "In production"
     (in "Testing" the login expires every 7 days, which breaks automation).
  3. Credentials: create an OAuth client of type "Desktop app", download the JSON,
     save it in this folder as client_secret.json.
  4. python -m clipper youtube-login     (opens Google; you approve access to your channel)
Vertical videos up to 3 minutes are published as Shorts automatically.
"""
import base64, hashlib, json, secrets, time, urllib.error, urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from .util import ROOT, cfg, load_json, save_json

SECRET, TOKEN = ROOT / "client_secret.json", ROOT / ".youtube_token.json"
SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
AUTH, TOKEN_URL = "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"


def ready():
    return SECRET.exists() and TOKEN.exists()


def client():
    j = load_json(SECRET)
    if not j:
        raise RuntimeError("client_secret.json not found (see the setup steps in clipper/youtube.py)")
    return j.get("installed") or j.get("web") or j


def token_request(fields):
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google sign-in failed: {e.read().decode()[:200]}")


def login():
    """Browser sign-in on this machine; the approval code comes back to a one-shot local listener."""
    c = client()
    got = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            got.update(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<h2>Clipper is connected to YouTube. You can close this tab.</h2>")

    srv = HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(16)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = AUTH + "?" + urllib.parse.urlencode({"client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code",
                                              "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
                                              "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Opening Google sign-in. If no browser opens, visit:\n" + url)
    webbrowser.open(url)
    srv.timeout = 300
    srv.handle_request()
    if got.get("state") != state or "code" not in got:
        raise RuntimeError("sign-in was cancelled or did not complete: " + got.get("error", "no code"))
    t = token_request({"client_id": c["client_id"], "client_secret": c.get("client_secret", ""), "code": got["code"],
                       "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect})
    t["expires_at"] = time.time() + t.get("expires_in", 3600) - 60
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                                 headers={"Authorization": f"Bearer {t['access_token']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        items = json.loads(r.read()).get("items") or []
    if not items:
        raise RuntimeError("that Google identity has no YouTube channel")
    t["channel_id"], t["channel_title"] = items[0]["id"], items[0]["snippet"]["title"]
    want = cfg().get("youtube", {}).get("channel_id")
    if want and t["channel_id"] != want:
        raise RuntimeError(f"you picked the channel \"{t['channel_title']}\", but config.toml expects {want}. "
                           "Run the login again and choose the right channel on Google's account screen.")
    save_json(TOKEN, t)
    TOKEN.chmod(0o600)
    return f"YouTube connected to channel: {t['channel_title']}"


def channel():
    t = load_json(TOKEN) or {}
    return t.get("channel_id"), t.get("channel_title")


def access_token():
    t = load_json(TOKEN)
    if not t:
        raise RuntimeError("not signed in to YouTube: run `python -m clipper youtube-login`")
    if time.time() > t.get("expires_at", 0):
        c = client()
        n = token_request({"client_id": c["client_id"], "client_secret": c.get("client_secret", ""),
                           "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
        t.update(n)
        t["expires_at"] = time.time() + n.get("expires_in", 3600) - 60
        save_json(TOKEN, t)
    return t["access_token"]


def upload(video, title, description, tags, synthetic=False):
    y = cfg().get("youtube", {})
    if y.get("channel_id") and channel()[0] != y["channel_id"]:   # never post to the wrong channel
        raise RuntimeError("signed in to a different channel than config.toml allows: run `python -m clipper youtube-login`")
    meta = {"snippet": {"title": title[:100] or "GTA 6 clip", "description": description[:4900], "tags": tags,
                        "categoryId": str(y.get("category", 20)), "defaultLanguage": y.get("language", "en"),
                        "defaultAudioLanguage": y.get("language", "en")},
            "status": {"privacyStatus": y.get("privacy", "public"), "selfDeclaredMadeForKids": False}}
    if synthetic:
        meta["status"]["containsSyntheticMedia"] = True
    head = {"Authorization": f"Bearer {access_token()}"}
    try:
        req = urllib.request.Request(UPLOAD, data=json.dumps(meta).encode(), method="POST",
                                     headers={**head, "Content-Type": "application/json; charset=UTF-8",
                                              "X-Upload-Content-Type": "video/mp4"})
        with urllib.request.urlopen(req, timeout=60) as r:
            session = r.headers["Location"]
        req = urllib.request.Request(session, data=video.read_bytes(), method="PUT", headers={**head, "Content-Type": "video/mp4"})
        with urllib.request.urlopen(req, timeout=900) as r:
            j = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read())["error"]["message"]
        except Exception:
            msg = e.reason
        if synthetic and e.code == 400 and "ynthetic" in str(msg):
            return upload(video, title, description, tags, synthetic=False)
        raise RuntimeError(f"YouTube {e.code}: {msg}")
    return {"id": j["id"], "url": f"https://www.youtube.com/shorts/{j['id']}", "privacy": (j.get("status") or {}).get("privacyStatus")}
