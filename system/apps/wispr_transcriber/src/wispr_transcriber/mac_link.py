"""The link to the user's Mac: the one place a Wispr Flow sign-in can come from.

Wispr Flow has no API key to hand out. The only credential that its transcription
servers accept is the sign-in token its desktop app keeps on the user's Mac, at
``~/Library/Application Support/Wispr Flow/session.json``. The desktop app renews that
token by itself while it runs; nothing in this workspace can renew it, and trying to
would rotate the token and risk signing the desktop app out.

So this module copies the token here and keeps the copy until it goes stale. It supports
three ways to reach ``session.json``, tried in order:

1. **Synced folder** (recommended): a read-only synchronized copy of
   ``~/Library/Application Support/Wispr Flow`` under ``~/synced_folders/`` (or pointed
   to by ``WISPR_FLOW_SUPPORT_DIR``). Zero network round trip and works even while the
   Mac is asleep.
2. **Direct File Sharing READ**: a read-only Imbue Studio file-sharing grant covering
   ``~/Library/Application Support/Wispr Flow``, read over the file server while the Mac
   is awake. No helper or daemon on the Mac is needed.
3. **Legacy Mac command bridge (``deskrun``)**: fallback for workspaces that already use
   ``local-print-bridge`` with ``~/tmp`` WRITE access.

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

# Scratch space on the Mac, inside the granted path, used only by the legacy deskrun fallback.
MAC_SCRATCH = "tmp/minds_data/wispr-link"
FILE_SERVER = "http://latchkey-self.invalid/minds-api-proxy/api/v1/files"
WISPR_SUPPORT_REL = "Library/Application Support/Wispr Flow"

# A token with less than this left is worth replacing while the Mac is reachable.
STALE_SECONDS = 12 * 3600
DESKRUN_TIMEOUT = 90

# deskrun frames the remote output; the footer carries the remote exit status.
_FOOTER = re.compile(r"^=== exit (-?\d+) ===$", re.MULTILINE)
_HEADER = re.compile(r"^=== .*\.cmd @ .* ===$", re.MULTILINE)
_FILE_GRANT_RE = re.compile(
    r"^minds-file-server-(?:read|write)-(?:(?P<device>host-[^:]+):)?(?P<path>/.*)$"
)


class MacLinkError(RuntimeError):
    """The Mac could not be reached, or it refused what was asked of it."""


class CredentialsMissing(RuntimeError):
    """There is no usable Wispr Flow sign-in in this workspace."""


def _home_candidates() -> list[Path]:
    """Candidate home directories (supervisord uses /home/user; some harnesses override HOME)."""
    candidates: list[Path] = []
    for path in (Path.home(), Path("/home/user")):
        if path not in candidates:
            candidates.append(path)
    return candidates


def _workbench_file(name: str) -> Path | None:
    """Locate a workbench config file across WORKBENCH_CONFIG and /home/user."""
    candidates = [WORKBENCH_CONFIG / name]
    for home in _home_candidates():
        candidate = home / ".config" / "latchkey-dev-workbench" / name
        if candidate not in candidates:
            candidates.append(candidate)
    for path in candidates:
        if path.exists() and path.read_text().strip():
            return path
    return None


def _list_devices() -> list[dict]:
    out = _latchkey(["curl", "-s", "http://latchkey-self.invalid/devices"])
    try:
        return json.loads(out).get("devices") or []
    except (json.JSONDecodeError, AttributeError) as error:
        raise MacLinkError(f"Unexpected response from /devices: {error}") from error


def mac_device() -> str:
    """The device id of the Mac to talk to."""
    from_env = os.environ.get("MINDS_MAC_DEVICE")
    if from_env:
        return from_env.strip()
    path = _workbench_file("mac_device")
    if path is not None:
        return path.read_text().strip()
    devices = _list_devices()
    if not devices:
        raise MacLinkError("No computer of yours is reachable from this workspace.")
    return devices[0]["device_id"]


def mac_home() -> str:
    """The home directory path on the Mac, used only by the legacy deskrun fallback."""
    from_env = os.environ.get("MINDS_MAC_HOME")
    if from_env:
        return from_env.rstrip("/")
    path = _workbench_file("mac_home")
    if path is not None:
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
            f"The Mac command bridge is not installed here ({DESKRUN} is missing). "
            "Use file sharing on ~/Library/Application Support/Wispr Flow instead."
        )
    env = {**os.environ, "DESKRUN_TIMEOUT": str(timeout)}
    if not (Path.home() / ".config/latchkey-dev-workbench").exists() and Path(
        "/home/user/.config/latchkey-dev-workbench"
    ).exists():
        env["HOME"] = "/home/user"
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


def mac_fetch_from(device: str, absolute_path: str) -> bytes:
    """Read a file from a specific Mac device through the Imbue Studio file server."""
    url = f"{FILE_SERVER}/{device}{urllib.parse.quote(absolute_path)}"
    out = _latchkey(["curl", "-sf", "-H", f"X-Latchkey-Device: {device}", url], binary=True, timeout=180)
    assert isinstance(out, bytes)
    if not out:
        raise MacLinkError(f"Nothing came back from {absolute_path} on your Mac.")
    return out


def mac_fetch(absolute_path: str) -> bytes:
    """Read a file from the default Mac through the file server."""
    return mac_fetch_from(mac_device(), absolute_path)


def extract_credentials(
    session_raw: dict,
    config_raw: dict | None = None,
    *,
    app_version: str | None = None,
    source: str | None = None,
) -> dict:
    """Extract the bearer token and metadata from Wispr Flow's session.json (and optional config.json)."""
    keys = [k for k in session_raw if k.startswith("sb-") and k.endswith("-auth-token")]
    if not keys:
        raise MacLinkError(
            "Wispr Flow's saved sign-in is not in the expected format (no sb-*-auth-token key)."
        )
    value = session_raw[keys[0]]
    session = json.loads(value) if isinstance(value, str) else value
    if not isinstance(session, dict):
        raise MacLinkError("Wispr Flow's saved sign-in could not be parsed.")
    token = session.get("access_token")
    if not token:
        raise MacLinkError("Wispr Flow's saved sign-in has no access token in it.")
    prefs = (config_raw or {}).get("prefs") or {}
    return {
        "access_token": token,
        "expires_at": session.get("expires_at"),
        "user_uuid": (prefs.get("user") or {}).get("uuid"),
        "session_id": prefs.get("sessionId"),
        "email": (session.get("user") or {}).get("email"),
        "app_version": app_version,
        "copied_at": int(time.time()),
        "source": source,
    }


def find_synced_support_dir() -> Path | None:
    """Find a locally synced (or explicitly configured) Wispr Flow support directory."""
    override = os.environ.get("WISPR_FLOW_SUPPORT_DIR")
    if override:
        path = Path(override).expanduser()
        if (path / "session.json").is_file():
            return path
        return None

    found: list[Path] = []
    for home in _home_candidates():
        root = home / "synced_folders"
        if not root.is_dir():
            continue
        for candidate in root.rglob("session.json"):
            if candidate.parent.name == "Wispr Flow" and candidate.is_file():
                found.append(candidate.parent)
    if not found:
        return None
    found.sort(key=lambda d: (d / "session.json").stat().st_mtime, reverse=True)
    return found[0]


def _resolve_support_dir_from_grant(granted_path: str) -> str | None:
    """If a file-sharing grant covers Wispr Flow's support directory, return that directory path."""
    norm = granted_path.rstrip("/")
    suffix_to_append = (
        ("/Library/Application Support/Wispr Flow", ""),
        ("/Library/Application Support", "/Wispr Flow"),
        ("/Library", "/Application Support/Wispr Flow"),
    )
    for suffix, extra in suffix_to_append:
        if norm.endswith(suffix):
            return norm + extra
    # A whole macOS home directory grant (/Users/<name>) also covers ~/Library/...
    parts = [p for p in norm.split("/") if p]
    if len(parts) == 2 and parts[0] == "Users":
        return f"{norm}/{WISPR_SUPPORT_REL}"
    return None


def _flatten_permission_rules(payload: dict) -> list[str]:
    """Extract all permission strings from latchkey's /permissions/self response."""
    result: list[str] = []
    for entry in payload.get("rules") or []:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, dict):
            for values in entry.values():
                if isinstance(values, list):
                    result.extend(v for v in values if isinstance(v, str))
    return result


def find_file_sharing_grant() -> tuple[str, str] | None:
    """Find a granted file-sharing path covering Wispr Flow's support dir: (device_id, support_dir)."""
    try:
        devices = _list_devices()
    except MacLinkError:
        return None
    device_ids: list[str] = []
    env_dev = os.environ.get("MINDS_MAC_DEVICE")
    if env_dev:
        device_ids.append(env_dev.strip())
    cfg_dev = _workbench_file("mac_device")
    if cfg_dev is not None:
        dev = cfg_dev.read_text().strip()
        if dev not in device_ids:
            device_ids.append(dev)
    for item in devices:
        dev = item.get("device_id")
        if dev and dev not in device_ids:
            device_ids.append(dev)

    matches: list[tuple[int, str, str]] = []
    for dev in device_ids:
        try:
            raw = _latchkey(
                ["curl", "-s", "-H", f"X-Latchkey-Device: {dev}", "http://latchkey-self.invalid/permissions/self"]
            )
            data = json.loads(raw)
        except (MacLinkError, json.JSONDecodeError):
            continue
        for rule in _flatten_permission_rules(data):
            m = _FILE_GRANT_RE.match(rule)
            if not m:
                continue
            rule_dev = m.group("device") or dev
            granted = m.group("path")
            support_dir = _resolve_support_dir_from_grant(granted)
            if support_dir:
                matches.append((len(granted), rule_dev, support_dir))
    if not matches:
        return None
    # Prefer the most specific grant (e.g. .../Wispr Flow over /Users/<name>).
    matches.sort(key=lambda item: item[0], reverse=True)
    _, best_device, best_dir = matches[0]
    return best_device, best_dir


def _refresh_from_synced(support_dir: Path) -> dict:
    session_path = support_dir / "session.json"
    if not session_path.is_file():
        raise MacLinkError(
            "Wispr Flow has no saved sign-in in the synced folder. Open Wispr Flow on your Mac and sign in."
        )
    try:
        session_raw = json.loads(session_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MacLinkError(f"Could not read Wispr Flow session.json: {error}") from error
    config_raw = None
    config_path = support_dir / "config.json"
    if config_path.is_file():
        try:
            config_raw = json.loads(config_path.read_text())
        except (OSError, json.JSONDecodeError):
            config_raw = None
    payload = extract_credentials(session_raw, config_raw, source="synced_folder")
    save(payload)
    return payload


def _refresh_from_file_sharing(device: str, support_dir: str) -> dict:
    try:
        session_bytes = mac_fetch_from(device, f"{support_dir}/session.json")
    except MacLinkError as error:
        raise MacLinkError(
            "Could not read Wispr Flow's session.json from your Mac. Make sure your Mac is awake, "
            "Imbue Studio is running, and Wispr Flow is signed in. "
            f"({error})"
        ) from error
    try:
        session_raw = json.loads(session_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MacLinkError(f"Wispr Flow's session.json on your Mac was not valid JSON: {error}") from error
    config_raw = None
    try:
        config_bytes = mac_fetch_from(device, f"{support_dir}/config.json")
        config_raw = json.loads(config_bytes.decode("utf-8"))
    except (MacLinkError, UnicodeDecodeError, json.JSONDecodeError):
        config_raw = None
    payload = extract_credentials(session_raw, config_raw, source="file_sharing")
    save(payload)
    return payload


def _deskrun_configured() -> bool:
    return DESKRUN.exists() and (
        bool(os.environ.get("MINDS_MAC_HOME")) or _workbench_file("mac_home") is not None
    )


def _refresh_from_deskrun() -> dict:
    """Legacy fallback: copy a fresh Wispr Flow sign-in through deskrun + ~/tmp."""
    home = mac_home()
    scratch = f"{home}/{MAC_SCRATCH}"
    reader = f"""
set -e
mkdir -p '{scratch}'
/usr/bin/python3 - '{scratch}/credentials.json' <<'PYEOF'
import json, os, sys
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
    "source": "deskrun",
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


def refresh() -> dict:
    """Copy a fresh Wispr Flow sign-in from the Mac into this workspace.

    Tries (1) a locally synced Wispr Flow folder, (2) a direct read-only Imbue Studio
    file-sharing grant on ``~/Library/Application Support/Wispr Flow``, and (3) the legacy
    ``deskrun`` bridge if installed.
    """
    synced = find_synced_support_dir()
    if synced is not None:
        return _refresh_from_synced(synced)

    grant = find_file_sharing_grant()
    if grant is not None:
        return _refresh_from_file_sharing(*grant)

    if _deskrun_configured():
        return _refresh_from_deskrun()

    raise MacLinkError(
        "No access to Wispr Flow's folder on your Mac is set up yet. Grant read-only file sharing "
        "for ~/Library/Application Support/Wispr Flow (with synced copy enabled) in Imbue Studio, "
        "then run `wispr-mac-link refresh`."
    )


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
    synced = find_synced_support_dir()
    deskrun_ready = _deskrun_configured()
    mac_ready = (synced is not None) or deskrun_ready
    if not creds:
        return {
            "signed_in": False,
            "needs_mac": True,
            "reason": "This workspace has no Wispr Flow sign-in yet.",
            "mac_link_installed": mac_ready,
            "synced_folder": str(synced) if synced else None,
            "deskrun_available": deskrun_ready,
            "source": None,
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
        "source": creds.get("source"),
        "synced_folder": str(synced) if synced else None,
        "deskrun_available": deskrun_ready,
        "mac_link_installed": mac_ready,
    }


def access_token(auto_refresh: bool = True) -> str:
    """The token to send to Wispr, refreshed first when it has to be."""
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
            "No Wispr Flow sign-in is stored here. Run `wispr-mac-link refresh` after sharing "
            "~/Library/Application Support/Wispr Flow."
        )
    expires_at = creds.get("expires_at")
    if expires_at and expires_at <= time.time():
        raise CredentialsMissing(
            "The stored Wispr Flow sign-in has expired. Open Wispr Flow on your Mac, then run "
            "`wispr-mac-link refresh`."
        )
    return creds["access_token"]


def say(text: str) -> bytes:
    """Have the Mac speak a sentence and return it as 16 kHz mono WAV (requires deskrun)."""
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
        via = payload.get("source") or "mac"
        print(f"Copied a sign-in for {payload.get('email')} via {via}, good for {left / 3600:.1f} hours.")
        return
    Path(args.out).write_bytes(say(args.text))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
