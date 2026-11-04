"""The link to the user's Mac: the one place a Wispr Flow sign-in can come from.

Wispr Flow has no API key to hand out. The only credential that its transcription
servers accept is the sign-in token its desktop app keeps on the user's Mac, at
``~/Library/Application Support/Wispr Flow/session.json``. The desktop app renews that
token by itself while it runs; nothing in this workspace can renew it, and trying to
would rotate the token and risk signing the desktop app out.

So this module copies the token here and keeps the copy until it goes stale. Reaching the
Mac needs two things, both already in place in this workspace and both described in the
repo's README:

* the ``local-print-bridge`` skill's ``deskrun``, which runs one shell command on the Mac;
* a file-sharing WRITE grant on ``~/tmp`` on the Mac, which is how bytes come back.

Transcribing does NOT need the Mac. Only refreshing the token does.
"""

import json
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

DESKRUN = Path(".agents/skills/local-print-bridge/bin/deskrun")
WORKBENCH_CONFIG = Path("~/.config/latchkey-dev-workbench").expanduser()
DATA_DIR = Path(os.environ.get("WISPR_TRANSCRIBER_DATA_DIR", "data/.apps/wispr-transcriber"))
CREDENTIALS_PATH = DATA_DIR / "credentials.json"

# Scratch space on the Mac, inside the granted path, used only to hand bytes back.
MAC_SCRATCH = "tmp/minds_data/wispr-link"
FILE_SERVER = "http://latchkey-self.invalid/minds-api-proxy/api/v1/files"

# A token with less than this left is worth replacing while the Mac is reachable.
STALE_SECONDS = 12 * 3600
DESKRUN_TIMEOUT = 90

# deskrun frames the remote output; the footer carries the remote exit status.
_FOOTER = re.compile(r"^=== exit (-?\d+) ===$", re.MULTILINE)
_HEADER = re.compile(r"^=== .*\.cmd @ .* ===$", re.MULTILINE)


class MacLinkError(RuntimeError):
    """The Mac could not be reached, or it refused what was asked of it."""


class CredentialsMissing(RuntimeError):
    """There is no usable Wispr Flow sign-in in this workspace."""


def mac_device() -> str:
    """The device id of the Mac to talk to."""
    from_env = os.environ.get("MINDS_MAC_DEVICE")
    if from_env:
        return from_env.strip()
    path = WORKBENCH_CONFIG / "mac_device"
    if path.exists() and path.read_text().strip():
        return path.read_text().strip()
    out = _latchkey(["curl", "-s", "http://latchkey-self.invalid/devices"])
    devices = json.loads(out).get("devices") or []
    if not devices:
        raise MacLinkError("No computer of yours is reachable from this workspace.")
    return devices[0]["device_id"]


def mac_home() -> str:
    """The home directory path on the Mac, which differs from this container's."""
    from_env = os.environ.get("MINDS_MAC_HOME")
    if from_env:
        return from_env.rstrip("/")
    path = WORKBENCH_CONFIG / "mac_home"
    if path.exists() and path.read_text().strip():
        return path.read_text().strip().rstrip("/")
    raise MacLinkError(
        f"The Mac's home directory is not configured. Write it to {WORKBENCH_CONFIG / 'mac_home'} "
        "(for example /Users/yourname)."
    )


def _latchkey(args: list[str], binary: bool = False, timeout: int = 60) -> str | bytes:
    proc = subprocess.run(["latchkey", *args], capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise MacLinkError(f"latchkey {args[0]} failed: {proc.stderr.decode(errors='replace')[:400]}")
    return proc.stdout if binary else proc.stdout.decode(errors="replace")


def deskrun(script: str, timeout: int = DESKRUN_TIMEOUT) -> str:
    """Run one shell command on the Mac and return what it printed.

    Raises MacLinkError when the bridge could not be reached or the command itself failed,
    so a caller never has to read the framing deskrun puts around the output.
    """
    if not DESKRUN.exists():
        raise MacLinkError(
            f"The connection to the Mac is not installed here ({DESKRUN} is missing). "
            "See this app's README for how to install it."
        )
    env = {**os.environ, "DESKRUN_TIMEOUT": str(timeout)}
    proc = subprocess.run(
        [str(DESKRUN)], input=script, capture_output=True, text=True, timeout=timeout + 30, env=env
    )
    if proc.returncode != 0:
        raise MacLinkError(
            "Could not reach your Mac. It may be asleep, or the Imbue Studio app may be closed. "
            f"({proc.stderr.strip()[:300]})"
        )
    body = _HEADER.sub("", proc.stdout)
    footer = _FOOTER.search(body)
    status = int(footer.group(1)) if footer else 0
    body = _FOOTER.sub("", body).strip("\n")
    if status != 0:
        raise MacLinkError(f"The command failed on your Mac (exit {status}): {body.strip()[:400]}")
    return body


def mac_fetch(absolute_path: str) -> bytes:
    """Read a file from the Mac through the file server."""
    device = mac_device()
    url = f"{FILE_SERVER}/{device}{urllib.parse.quote(absolute_path)}"
    out = _latchkey(["curl", "-sf", "-H", f"X-Latchkey-Device: {device}", url], binary=True, timeout=180)
    assert isinstance(out, bytes)
    if not out:
        raise MacLinkError(f"Nothing came back from {absolute_path} on your Mac.")
    return out


def refresh() -> dict:
    """Copy a fresh Wispr Flow sign-in from the Mac into this workspace.

    The token is handed over as a file rather than printed, so it never lands in the
    bridge's command log on the Mac. The scratch copy is deleted either way.
    """
    home = mac_home()
    scratch = f"{home}/{MAC_SCRATCH}"
    reader = f"""
set -e
mkdir -p '{scratch}'
/usr/bin/python3 - '{scratch}/credentials.json' <<'PYEOF'
import glob, json, os, sys
support = os.path.expanduser("~/Library/Application Support/Wispr Flow")
session_path = os.path.join(support, "session.json")
if not os.path.exists(session_path):
    sys.exit("Wispr Flow has no saved sign-in on this Mac. Open Wispr Flow and sign in.")
raw = json.load(open(session_path))
keys = [k for k in raw if k.startswith("sb-") and k.endswith("-auth-token")]
if not keys:
    sys.exit("Wispr Flow's saved sign-in is not in the expected format (no sb-*-auth-token key).")
value = raw[keys[0]]
session = json.loads(value) if isinstance(value, str) else value
token = session.get("access_token")
if not token:
    sys.exit("Wispr Flow's saved sign-in has no access token in it.")
prefs = {{}}
try:
    prefs = json.load(open(os.path.join(support, "config.json"))).get("prefs", {{}}) or {{}}
except Exception:
    pass
out = {{
    "access_token": token,
    "expires_at": session.get("expires_at"),
    "user_uuid": (prefs.get("user") or {{}}).get("uuid"),
    "session_id": prefs.get("sessionId"),
    "email": (session.get("user") or {{}}).get("email"),
    "app_version": None,
    "copied_at": int(__import__("time").time()),
}}
try:
    import plistlib
    plist = "/Applications/Wispr Flow.app/Contents/Info.plist"
    out["app_version"] = plistlib.load(open(plist, "rb")).get("CFBundleShortVersionString")
except Exception:
    pass
path = sys.argv[1]
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as handle:
    json.dump(out, handle)
print("WROTE")
PYEOF
"""
    deskrun(reader)
    try:
        payload = json.loads(mac_fetch(f"{scratch}/credentials.json"))
    finally:
        try:
            deskrun(f"rm -f '{scratch}/credentials.json'")
        except MacLinkError:
            pass
    if not payload.get("access_token"):
        raise MacLinkError("The sign-in that came back from your Mac had no token in it.")
    save(payload)
    return payload


def save(payload: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(CREDENTIALS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(payload, handle)


def load() -> dict | None:
    if not CREDENTIALS_PATH.exists():
        return None
    try:
        return json.loads(CREDENTIALS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def status() -> dict:
    """What this workspace currently holds, and whether the Mac is needed."""
    creds = load()
    mac_ready = DESKRUN.exists() and (WORKBENCH_CONFIG / "mac_home").exists()
    if not creds:
        return {
            "signed_in": False,
            "needs_mac": True,
            "reason": "This workspace has no Wispr Flow sign-in yet.",
            "mac_link_installed": mac_ready,
        }
    expires_at = creds.get("expires_at")
    left = int(expires_at - time.time()) if expires_at else None
    expired = left is not None and left <= 0
    stale = left is not None and left < STALE_SECONDS
    return {
        "signed_in": not expired,
        "needs_mac": expired or stale,
        "reason": (
            "The Wispr Flow sign-in has expired." if expired
            else "The sign-in runs out soon; worth replacing while the Mac is awake." if stale
            else "The sign-in is current."
        ),
        "expires_at": expires_at,
        "seconds_left": left,
        "email": creds.get("email"),
        "app_version": creds.get("app_version"),
        "copied_at": creds.get("copied_at"),
        "mac_link_installed": mac_ready,
    }


def access_token(auto_refresh: bool = True) -> str:
    """The token to send to Wispr, refreshed from the Mac first when it has to be."""
    state = status()
    if auto_refresh and state["needs_mac"]:
        try:
            refresh()
        except MacLinkError:
            if not state["signed_in"]:
                raise
    creds = load()
    if not creds or not creds.get("access_token"):
        raise CredentialsMissing(
            "No Wispr Flow sign-in is stored here. Run `wispr-mac-link refresh` while your Mac is awake."
        )
    expires_at = creds.get("expires_at")
    if expires_at and expires_at <= time.time():
        raise CredentialsMissing(
            "The stored Wispr Flow sign-in has expired. Open Wispr Flow on your Mac, then run "
            "`wispr-mac-link refresh`."
        )
    return creds["access_token"]


def say(text: str) -> bytes:
    """Have the Mac speak a sentence and return it as 16 kHz mono WAV.

    This is the demo's stand-in for a microphone: workspace windows cannot reach one, and
    recording the Mac's real microphone would need a permission grant the user has to make.
    """
    import base64

    home = mac_home()
    scratch = f"{home}/{MAC_SCRATCH}"
    encoded = base64.b64encode(text.encode()).decode()
    script = f"""
set -e
mkdir -p '{scratch}'
/usr/bin/python3 -c 'import base64,subprocess,sys; subprocess.run(["say","-o",sys.argv[2],"--data-format=LEI16@16000",base64.b64decode(sys.argv[1]).decode()],check=True)' '{encoded}' '{scratch}/say.wav'
echo SPOKEN
"""
    deskrun(script)
    try:
        return mac_fetch(f"{scratch}/say.wav")
    finally:
        try:
            deskrun(f"rm -f '{scratch}/say.wav'")
        except MacLinkError:
            pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Manage this workspace's Wispr Flow sign-in.")
    parser.add_argument("action", choices=["status", "refresh", "say"])
    parser.add_argument("--text", default="Testing the connection to Wispr Flow.")
    parser.add_argument("--out", default="mac-say.wav")
    args = parser.parse_args()

    if args.action == "status":
        print(json.dumps(status(), indent=1))
        return
    if args.action == "refresh":
        payload = refresh()
        left = payload.get("expires_at", 0) - time.time()
        print(f"Copied a sign-in for {payload.get('email')}, good for {left / 3600:.1f} hours.")
        return
    Path(args.out).write_bytes(say(args.text))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
