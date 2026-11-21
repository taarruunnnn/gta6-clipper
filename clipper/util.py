"""Shared helpers: config, paths, subprocess with background priority, clip records."""
import hashlib, json, os, re, shutil, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"


def _merge(a, b):
    for k, v in b.items():
        a[k] = _merge(a[k], v) if isinstance(v, dict) and isinstance(a.get(k), dict) else v
    return a


def cfg():
    """config.toml holds the defaults; config.local.toml (never committed) holds your own channel's overrides."""
    with open(ROOT / "config.toml", "rb") as f:
        c = tomllib.load(f)
    local = ROOT / "config.local.toml"
    if local.exists():
        with open(local, "rb") as f:
            _merge(c, tomllib.load(f))
    return c


def ask_llm(prompt, cwd=None, model="sonnet", timeout=240):
    """Send a prompt to the configured command-line assistant and return its text reply.
    Default is Claude Code (`claude -p`); change [llm] command in config to use another CLI."""
    cmd = cfg().get("llm", {}).get("command") or ["claude", "-p", "{prompt}", "--model", "{model}", "--allowedTools", "Read"]
    cmd = [c.replace("{prompt}", prompt).replace("{model}", model) for c in cmd]
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"`{cmd[0]}` was not found: install it or set [llm] command in config.toml")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("assistant call failed: " + (r.stderr or r.stdout)[-300:])
    return r.stdout


def llm_json(prompt, **kw):
    """ask_llm, then the first JSON object in the reply."""
    out = ask_llm(prompt, **kw)
    at = out.find("{")
    if at < 0:
        raise RuntimeError("assistant gave no JSON: " + out[-200:])
    return json.JSONDecoder().raw_decode(out[at:])[0]


def video_encoder():
    """Hardware H.264 where there is one: Apple VideoToolbox, NVIDIA NVENC, otherwise software x264."""
    bitrate = cfg()["performance"]["bitrate"]
    if sys.platform == "darwin":
        return ["-hwaccel", "videotoolbox"], ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-profile:v", "high"]
    if shutil.which("nvidia-smi"):
        enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        if "h264_nvenc" in enc:
            return [], ["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", bitrate]
    return [], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def env(key, default=""):
    """Secrets come from the environment or a local .env file (never from config.toml)."""
    if os.environ.get(key):
        return os.environ[key]
    p = ROOT / ".env"
    if p.exists():
        for ln in p.read_text().splitlines():
            if ln.strip().startswith(key + "="):
                return ln.split("=", 1)[1].strip().strip("\"'")
    return default


def background():
    return cfg()["performance"].get("mode", "background") == "background"


def be_nice():
    """Lower this process's own priority (used for in-process work such as drawing overlays)."""
    if background():
        try:
            os.nice(10)
        except OSError:
            pass


def run(cmd, heavy=False, capture=False, check=True, **kw):
    """Run a command. heavy=True puts it on the efficiency cores at background priority (macOS)."""
    cmd = [str(c) for c in cmd]
    if heavy and background():
        if sys.platform == "darwin" and shutil.which("taskpolicy"):
            cmd = ["taskpolicy", "-b"] + cmd          # efficiency cores, background priority
        elif sys.platform.startswith("linux") and shutil.which("nice"):
            cmd = ["nice", "-n", "10"] + cmd
    r = subprocess.run(cmd, text=True, capture_output=capture, **kw)
    if check and r.returncode != 0:
        tail = (r.stderr or "")[-1500:] if capture else ""
        raise RuntimeError(f"command failed ({r.returncode}): {' '.join(cmd[:4])} ...\n{tail}")
    return r


def ytdlp(*args, **kw):
    return run([sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings", "-q", "--no-progress", *args], **kw)


def ffprobe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], capture=True)
    return json.loads(r.stdout)


def duration(path):
    return float(ffprobe(path)["format"]["duration"])


def is_url(s):
    return bool(re.match(r"https?://", str(s)))


def job_dir(src):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(src).split("//")[-1])[-40:].strip("-")
    d = JOBS / f"{slug}-{hashlib.sha1(str(src).encode()).hexdigest()[:6]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(p, default=None):
    p = Path(p)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save_json(p, obj):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


# ---- clip records: jobs/<job>/clips/<n>/clip.json -------------------------------------------

def clip_dir(clip_id):
    job, n = clip_id.split("/")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", job) or not re.fullmatch(r"\d+", n):
        raise ValueError("bad clip id")
    return JOBS / job / "clips" / n


def load_clip(clip_id):
    c = load_json(clip_dir(clip_id) / "clip.json")
    if c is None:
        raise FileNotFoundError(clip_id)
    return c


def save_clip(c):
    save_json(clip_dir(c["id"]) / "clip.json", c)


def all_clips():
    out = []
    for p in JOBS.glob("*/clips/*/clip.json"):
        try:
            out.append(load_json(p))
        except Exception:
            pass
    out.sort(key=lambda c: c.get("created", 0), reverse=True)
    return out


def next_clip_number(job):
    nums = [int(p.name) for p in (job / "clips").glob("*") if p.name.isdigit()]
    return max(nums, default=0) + 1


def fmt_t(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def yt_search_today(query, limit=25):
    """Today's uploads for a query, most viewed first (flat entries: id, title, duration, view_count, channel)."""
    import urllib.parse
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}&sp=CAMSBAgCEAE%3D&hl=en&gl=US"
    j = json.loads(ytdlp("-J", "--flat-playlist", "--extractor-args", "youtube:lang=en", "--playlist-end", str(limit), url, capture=True).stdout)
    return [e for e in j.get("entries") or [] if e.get("id")]
