"""The Wispr Transcriber page: give it audio, watch it come back as text from Wispr Flow.

Runs from /home/user/workspace (the repo root). Every run is kept on disk under ``DATA_DIR``
(``data/.apps/wispr-transcriber/`` unless ``WISPR_TRANSCRIBER_DATA_DIR`` overrides it): the
audio exactly as it was sent, Wispr's own replies in full, and the summary the page shows.
Nothing is re-fetched to redraw a past run.

The page shows two texts side by side, because the difference between them is the whole
point: the left is what Wispr heard word for word, the right is what Wispr's own formatting
pass made of it. See the repo README for how the connection works.
"""

import base64
import difflib
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.serving import run_simple

from wispr_transcriber import audio, flow_client, mac_link

DATA_DIR = Path(os.environ.get("WISPR_TRANSCRIBER_DATA_DIR", "data/.apps/wispr-transcriber"))
PORT = int(os.environ.get("WISPR_TRANSCRIBER_PORT", "8122"))
RUNS_DIR = DATA_DIR / "runs"
ASSETS = Path(__file__).parent / "assets"

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_SPEAK_CHARS = 400
# The service caps a single stream; keep well inside it.
MAX_AUDIO_SECONDS = 240

SHELL_STATIC_MODULES_DIR = Path("system/apps/system_interface/imbue/system_interface/static/_static")
SHELL_STATIC_MODULE_NAMES = ("app_contract.js", "context_menu.js")
SHELL_PAGE_SCRIPT = """<script type="module">
  import { connectToShell } from "/_static/app_contract.js";
  import { installElementContextMenu } from "/_static/context_menu.js";
  let handshake = null;
  const connection = connectToShell({
    onHandshake: (received) => {
      handshake = received;
      connection.location(location.pathname + location.search, document.title);
    },
  });
  installElementContextMenu({ connection, handshake: () => handshake });
</script>"""
_SHELL_SCRIPT_HASH = base64.b64encode(
    hashlib.sha256(
        SHELL_PAGE_SCRIPT.removeprefix('<script type="module">').removesuffix("</script>").encode()
    ).digest()
).decode()
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    f"script-src 'self' 'sha256-{_SHELL_SCRIPT_HASH}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "media-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors *; "
    "base-uri 'none'; "
    "object-src 'none'"
)

app = Flask("wispr_transcriber", static_folder=None)
RUN_LOCK = threading.Lock()


@app.errorhandler(HTTPException)
def json_error(error: HTTPException) -> tuple[Response, int]:
    return jsonify({"detail": error.description}), error.code or 500


@app.after_request
def set_security_headers(response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


# ---- Runs on disk -----------------------------------------------------------------------------


def run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", run_id):
        abort(404)
    path = RUNS_DIR / run_id
    if not path.is_dir():
        abort(404)
    return path


def save_run(summary: dict, wav: bytes, responses: list[dict]) -> dict:
    """Keep the audio as sent, Wispr's full replies, and the summary the page draws."""
    with RUN_LOCK:
        directory = RUNS_DIR / summary["id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "audio.wav").write_bytes(wav)
        (directory / "responses.json").write_text(json.dumps(responses, indent=1))
        (directory / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def list_runs(limit: int = 30) -> list[dict]:
    if not RUNS_DIR.is_dir():
        return []
    summaries = []
    for directory in RUNS_DIR.iterdir():
        path = directory / "summary.json"
        if path.is_file():
            try:
                summaries.append(json.loads(path.read_text()))
            except json.JSONDecodeError:
                continue
    summaries.sort(key=lambda item: item.get("at", 0), reverse=True)
    return summaries[:limit]


# ---- Showing what Wispr changed ---------------------------------------------------------------


def word_diff(before: str, after: str) -> dict:
    """Mark up the two texts so Wispr's own edits are visible side by side.

    Without this the page shows two paragraphs that look alike and prove nothing. With it,
    the fillers Wispr dropped and the words it rewrote are the first thing the user sees.
    """
    tokens_before = re.findall(r"\S+\s*", before)
    tokens_after = re.findall(r"\S+\s*", after)
    matcher = difflib.SequenceMatcher(
        a=[token.strip().lower().strip(".,!?") for token in tokens_before],
        b=[token.strip().lower().strip(".,!?") for token in tokens_after],
        autojunk=False,
    )
    left: list[dict] = []
    right: list[dict] = []
    edits = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        chunk_before = "".join(tokens_before[i1:i2])
        chunk_after = "".join(tokens_after[j1:j2])
        if tag == "equal":
            left.append({"text": chunk_before, "kind": "same"})
            right.append({"text": chunk_after, "kind": "same"})
            continue
        edits += 1
        if chunk_before:
            left.append({"text": chunk_before, "kind": "dropped" if tag == "delete" else "changed"})
        if chunk_after:
            right.append({"text": chunk_after, "kind": "added" if tag == "insert" else "changed"})
    return {"left": left, "right": right, "edits": edits}


# ---- Doing one transcription ------------------------------------------------------------------


def transcribe_bytes(data: bytes, source: str, detail: str = "") -> dict:
    """Convert, send to Wispr, save everything, and return what the page needs."""
    pcm = audio.to_pcm(data)
    seconds = audio.duration_seconds(pcm)
    if seconds < 0.3:
        abort(400, "That clip is too short to transcribe. Try at least a second of speech.")
    if seconds > MAX_AUDIO_SECONDS:
        abort(400, f"That clip is {seconds / 60:.1f} minutes long; keep it under four minutes.")

    try:
        token = mac_link.access_token(auto_refresh=True)
    except (mac_link.CredentialsMissing, mac_link.MacLinkError) as error:
        abort(409, str(error))

    creds = mac_link.load() or {}
    try:
        result = flow_client.transcribe(pcm, token, token_owner=creds, app_name="Wispr Transcriber")
    except flow_client.FlowError as error:
        abort(502, str(error))

    wav = audio.to_wav(pcm)
    summary = {
        "id": str(uuid.uuid4()),
        "at": int(time.time()),
        "source": source,
        "detail": detail,
        "formatted": result.formatted.strip(),
        "raw": result.raw.strip(),
        "diff": word_diff(result.raw.strip(), result.formatted.strip()),
        "status": result.status,
        "origin": result.origin,
        "server": result.server,
        "audio_seconds": round(result.audio_seconds or seconds, 2),
        "round_trip_seconds": round(result.round_trip_seconds, 2),
        "server_total_seconds": round(result.server_total_seconds, 3),
        "messages": result.messages,
        "account": creds.get("email"),
        "app_version": creds.get("app_version"),
    }
    return save_run(summary, wav, result.responses)


# ---- Routes -----------------------------------------------------------------------------------


@app.route("/")
def index() -> Response:
    """The page: give it audio, see the text Wispr sends back."""
    html = (ASSETS / "app.html").read_text()
    return Response(html.replace("<!-- shell script -->", SHELL_PAGE_SCRIPT), mimetype="text/html")


@app.route("/api/status")
def api_status() -> Response:
    """Whether this workspace holds a usable Wispr Flow sign-in, and whether the Mac is needed."""
    state = mac_link.status()
    state["ffmpeg"] = bool(__import__("shutil").which("ffmpeg"))
    return jsonify(state)


@app.route("/api/health")
def api_health() -> Response:
    """A one-line verdict on whether the app can transcribe right now."""
    state = mac_link.status()
    ok = bool(state.get("signed_in")) and bool(__import__("shutil").which("ffmpeg"))
    return jsonify({"ok": ok, "signed_in": state.get("signed_in"), "reason": state.get("reason")})


_SHARE_LINK: dict[str, str | None] = {}


@app.route("/api/share-link")
def api_share_link() -> Response:
    """Where this page can be opened in an ordinary browser tab, which is the one place a
    microphone works: app windows in the workspace are not allowed to use one."""
    if "url" not in _SHARE_LINK:
        url = None
        try:
            import subprocess

            result = subprocess.run(
                ["uv", "run", "--no-sync", "workspace-layout", "list", "--json"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                for entry in json.loads(result.stdout).get("apps", []):
                    if entry.get("name") == "wispr-transcriber":
                        url = entry.get("link")
        except (OSError, ValueError, subprocess.SubprocessError):
            url = None
        _SHARE_LINK["url"] = url
    return jsonify({"url": _SHARE_LINK["url"]})


@app.route("/api/refresh-signin", methods=["POST"])
def api_refresh_signin() -> Response:
    """Copy a fresh Wispr Flow sign-in from the Mac. Needs the Mac awake with Imbue Studio open."""
    try:
        mac_link.refresh()
    except mac_link.MacLinkError as error:
        abort(502, str(error))
    return jsonify(mac_link.status())


@app.route("/api/transcribe", methods=["POST"])
def api_transcribe() -> Response:
    """Transcribe an uploaded recording, in any format ffmpeg reads."""
    upload = request.files.get("audio") or request.files.get("file")
    if upload is None:
        abort(400, "No audio file came with that request.")
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        abort(413, "That file is over 40 MB. Trim the recording and try again.")
    try:
        return jsonify(transcribe_bytes(data, "upload", upload.filename or "recording"))
    except audio.AudioError as error:
        abort(400, str(error))


@app.route("/v1/audio/transcriptions", methods=["POST"])
def openai_audio_transcriptions() -> Response:
    """OpenAI Whisper-compatible drop-in transcription endpoint.

    Accepts multipart ``file`` (or ``audio``) and optional ``response_format``
    (``json`` default -> ``{"text": ...}``, ``text`` -> plain text,
    ``verbose_json`` -> full summary with ``text``).
    """
    upload = request.files.get("file") or request.files.get("audio")
    if upload is None:
        abort(400, "No audio file came with that request (expected multipart field 'file').")
    data = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        abort(413, "That file is over 40 MB. Trim the recording and try again.")
    response_format = (request.form.get("response_format") or "json").strip().lower()
    if response_format not in ("json", "text", "verbose_json"):
        abort(400, f"Unsupported response_format {response_format!r}; use json, text, or verbose_json.")
    try:
        summary = transcribe_bytes(data, "api", upload.filename or "recording")
    except audio.AudioError as error:
        abort(400, str(error))
    text = summary.get("formatted") or summary.get("raw") or ""
    if response_format == "text":
        return Response(text, mimetype="text/plain; charset=utf-8")
    if response_format == "verbose_json":
        return jsonify({**summary, "text": text})
    return jsonify({"text": text})


@app.route("/api/speak", methods=["POST"])
def api_speak() -> Response:
    """Have the Mac speak a sentence out loud, then transcribe that recording."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        abort(400, "Write a sentence for your Mac to say.")
    if len(text) > MAX_SPEAK_CHARS:
        abort(400, f"Keep it under {MAX_SPEAK_CHARS} characters.")
    try:
        wav = mac_link.say(text)
    except mac_link.MacLinkError as error:
        abort(502, str(error))
    try:
        return jsonify(transcribe_bytes(wav, "mac", text))
    except audio.AudioError as error:
        abort(400, str(error))


@app.route("/api/sample", methods=["POST"])
def api_sample() -> Response:
    """Transcribe the clip that ships with the app, which needs no Mac and no upload."""
    try:
        return jsonify(transcribe_bytes((ASSETS / "sample.wav").read_bytes(), "sample", "built-in clip"))
    except audio.AudioError as error:
        abort(400, str(error))


@app.route("/api/runs")
def api_runs() -> Response:
    """Every past run, newest first."""
    return jsonify({"runs": list_runs()})


@app.route("/api/runs/<run_id>/audio")
def api_run_audio(run_id: str) -> Response:
    """The audio exactly as it was sent to Wispr, so it can be played back."""
    return send_file((run_dir(run_id) / "audio.wav").absolute(), mimetype="audio/wav")


@app.route("/api/runs/<run_id>/raw")
def api_run_raw(run_id: str) -> Response:
    """Wispr's own replies in full, unprocessed, as they came off the stream."""
    return Response((run_dir(run_id) / "responses.json").read_text(), mimetype="application/json")


@app.route("/app.js")
def page_script() -> Response:
    """This page's own script, served from the app's origin so the page's rules admit it."""
    return send_file((ASSETS / "app.js").absolute(), mimetype="text/javascript")


@app.route("/_static/<basename>")
def shell_module(basename: str) -> Response:
    """The workspace shell's page modules, served from this app's own origin."""
    if basename not in SHELL_STATIC_MODULE_NAMES:
        abort(404)
    module_path = SHELL_STATIC_MODULES_DIR / basename
    if not module_path.is_file():
        abort(404)
    return send_file(module_path.absolute(), mimetype="text/javascript")


@app.route("/health")
def health() -> Response:
    """Whether the service itself is up."""
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_simple("127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False)


if __name__ == "__main__":
    main()
