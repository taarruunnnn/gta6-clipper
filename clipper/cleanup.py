"""Keeps the jobs folder from growing forever.

Footage is the heavy part; the small clip.json records are always kept, because they are what stops the loop
re-clipping the same moment and what the learning report reads.
  - rejected / discarded clips: footage deleted after [cleanup] rejected_hours
  - clips that were kept but never posted and are now stale: same
  - posted clips: source and working files deleted straight away, the finished video after published_days (0 = right after the upload)
  - downloaded stream audio and chat: deleted after source_hours; orphaned live-recording buffers after 20 minutes
"""
import shutil, time
from .util import JOBS, ROOT, cfg, load_json

KEEP = {"clip.json"}


def size(p):
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else (p.stat().st_size if p.exists() else 0)


def remove(p, dry):
    n = size(p)
    if not dry:
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    return n


def run(dry=False, log=print):
    C = cfg().get("cleanup", {})
    A = cfg().get("auto", {})
    now, freed, touched = time.time(), 0, 0
    old_reject = C.get("rejected_hours", 1) * 3600
    old_post = C.get("published_days", 7) * 86400
    old_source = C.get("source_hours", 2) * 3600
    stale = (A.get("max_age_hours", 48) + 6) * 3600
    for cj in JOBS.glob("*/clips/*/clip.json"):
        c = load_json(cj)
        if not c:
            continue
        d, age, status = cj.parent, now - c.get("created", now), c.get("status")
        posted = [v["at"] for v in (c.get("posts") or {}).values() if v.get("ok")]
        if status in ("rejected", "discarded") and age > old_reject or status in ("candidate", "rendered") and age > stale:
            drop = [f for f in d.iterdir() if f.name not in KEEP]
        elif status == "published" and posted:
            keep = KEEP | ({"out.mp4", "thumb.jpg"} if now - min(posted) < old_post else set())
            drop = [f for f in d.iterdir() if f.name not in keep]
        else:
            continue
        got = sum(remove(f, dry) for f in drop)
        freed += got
        touched += 1 if got else 0
    for job in JOBS.glob("*"):
        if not job.is_dir():
            continue
        for f in list(job.glob("audio.*")) + list(job.glob("chat*")):
            if f.is_file() and now - f.stat().st_mtime > old_source:
                freed += remove(f, dry)
        for f in job.glob("live/*"):     # a running watcher prunes its own buffer; anything untouched for 20 minutes is orphaned
            if f.is_file() and now - f.stat().st_mtime > 20 * 60:
                freed += remove(f, dry)
    out = ROOT / cfg()["publish"]["outbox"]
    if out.is_dir():
        for f in out.iterdir():
            if f.is_file() and now - f.stat().st_mtime > old_post:
                freed += remove(f, dry)
    log(f"cleanup {'would free' if dry else 'freed'} {freed / 1e6:,.0f} MB from {touched} clips; jobs folder is now {size(JOBS) / 1e6:,.0f} MB")
    return freed
