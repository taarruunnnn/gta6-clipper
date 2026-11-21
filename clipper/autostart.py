"""Start the automation loop at login and restart it if it crashes (a macOS LaunchAgent).

  python -m clipper autostart on | off | status
The Mac still has to be awake: the loop holds it awake while it runs, but a closed lid on battery sleeps anyway.
"""
import os, plistlib, subprocess, sys
from pathlib import Path
from .util import ROOT

LABEL = "com.streamclipper.auto"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
DOMAIN = f"gui/{os.getuid()}"


LOG = Path.home() / "Library" / "Logs" / "stream-clipper" / "auto.log"


def spec():
    path = ":".join([str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    LOG.parent.mkdir(parents=True, exist_ok=True)
    # launchd itself may not touch ~/Documents, so it only starts a shell; the shell changes into the project
    cmd = f'cd "{ROOT}" && exec /usr/bin/caffeinate -i "{sys.executable}" -u -m clipper auto'
    return {"Label": LABEL, "ProgramArguments": ["/bin/sh", "-c", cmd], "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},      # restart after a crash, not after a clean stop
            "ThrottleInterval": 60, "ProcessType": "Background",
            "StandardOutPath": str(LOG), "StandardErrorPath": str(LOG),
            "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home()), "LANG": "en_US.UTF-8"}}


def on():
    if sys.platform != "darwin":
        return "autostart is built for macOS. On Linux use a systemd user service, on Windows Task Scheduler, running: python -m clipper auto"
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], capture_output=True)
    with open(PLIST, "wb") as f:
        plistlib.dump(spec(), f)
    r = subprocess.run(["launchctl", "bootstrap", DOMAIN, str(PLIST)], capture_output=True, text=True)
    return "autostart is on: the loop starts at login and restarts after a crash" if r.returncode == 0 else f"could not load: {r.stderr.strip()}"


def off():
    subprocess.run(["launchctl", "bootout", f"{DOMAIN}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    return "autostart is off and the loop is stopped"


def status():
    r = subprocess.run(["launchctl", "print", f"{DOMAIN}/{LABEL}"], capture_output=True, text=True)
    if r.returncode != 0:
        return "autostart is off"
    keep = [ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith(("state =", "pid =", "last exit code ="))]
    return "autostart is on\n" + "\n".join(keep)
