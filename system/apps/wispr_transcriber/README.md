# Wispr Transcriber

Turn speech into text using your own Wispr Flow account, from inside an Imbue Studio
workspace, as a drop-in speech-to-text service for any web app, phone app, or script.

Wispr Flow has an official API, but it is closed: their docs say they are not taking new
partners. This takes a different path. The Wispr Flow desktop app on your Mac talks to
Wispr's own transcription servers, and those servers accept nothing but the sign-in token
the app keeps on disk at `~/Library/Application Support/Wispr Flow/session.json`. This
workspace reads that token (read-only, through Imbue Studio file sharing) and makes the
same streaming calls the desktop app makes. Nothing on the Mac is modified, patched, or
installed.

Built and verified against Wispr Flow 1.6.1376 on macOS 15.6.

---

## 1. What you get

| Thing | How to reach it | Needs the Mac? |
| --- | --- | --- |
| Drop-in HTTP API | `POST http://127.0.0.1:8122/api/transcribe` (field `audio`) | No |
| OpenAI Whisper-compatible API | `POST http://127.0.0.1:8122/v1/audio/transcriptions` (field `file`) | No |
| Web page (record / upload / history) | runs as the `wispr-transcriber` app on port `8122` | No |
| CLI: transcribe any audio file | `wispr-transcribe recording.m4a` | No |
| CLI: end-to-end self-test | `wispr-transcribe --check` | No |
| CLI: inspect / refresh stored sign-in | `wispr-mac-link status` / `refresh` | `refresh` only (none if synced) |
| Python library | `from wispr_transcriber import audio, flow_client, mac_link` | No |

The app sets `stop_when_no_windows = false` in `app.toml`, so the HTTP server stays up
for other workspace apps even when the Wispr Transcriber window itself is closed.

---

## 2. One-time setup (for an adopting agent)

**All you need on the Mac is Wispr Flow installed and signed in, plus one read-only
file-sharing grant.** No Mac scripts, no Terminal commands, no helper daemon.

### Step 1: Request read-only access to Wispr Flow's folder on the Mac

First find the Mac's `device_id`:

```bash
latchkey curl -s http://latchkey-self.invalid/devices
```

Then file ONE file-sharing permission request for `~/Library/Application Support/Wispr Flow`
with `READ` access and a synchronized copy turned on (the sync keeps `session.json` fresh
automatically whenever Wispr Flow runs on the Mac, and lets transcription keep working even
while the Mac is asleep):

```bash
# Run as its own standalone tool call (output untouched) so Imbue Studio renders the approval card.
latchkey curl -H "X-Latchkey-Device: <DEVICE_ID>" -XPOST http://latchkey-self.invalid/permission-requests \
  -H 'Content-Type: application/json' \
  -d '{"agent_id": "'"${MINDS_CHAT_ID:-$MNGR_AGENT_ID}"'", "type": "file-sharing", "payload": {"path": "~/Library/Application Support/Wispr Flow", "access": "READ", "sync": {"conflict": "NEWER"}}, "rationale": "Read your Wispr Flow desktop sign-in token (read-only) so this workspace can transcribe speech through your own Wispr Flow account."}'
```

When the user approves:
- **If they kept the synchronized copy ON** (recommended): Imbue Studio mirrors the folder to
  `~/synced_folders/<device_id>/Users/<name>/Library/Application Support/Wispr Flow/`.
  `wispr-mac-link` discovers it automatically with zero extra configuration.
- **If they turned the synchronized copy OFF**: `wispr-mac-link` discovers the
  `minds-file-server-read` grant automatically from `/permissions/self` and reads
  `session.json` over the file server while the Mac is awake.

### Step 2: Confirm ffmpeg and the app are installed

In a template workspace, `ffmpeg` is declared in `template.toml` and the supervisord
program installs the app automatically. If installing manually from the standalone repo:

```bash
apt-get install -y --no-install-recommends ffmpeg
uv tool install -e system/apps/wispr_transcriber --reinstall
```

### Step 3: Pull the sign-in and verify end to end

Run these in order and stop at the first failure:

```bash
# 1. Copy the Wispr Flow sign-in from the shared/synced folder
wispr-mac-link refresh

# 2. Check status: expect "signed_in": true, "needs_mac": false
wispr-mac-link status

# 3. End-to-end real transcription on the built-in clip: expect word-for-word + cleaned text
wispr-transcribe --check

# 4. Check the HTTP service: expect {"ok": true, "signed_in": true, ...}
curl -s http://127.0.0.1:8122/api/health
```

What a passing step 3 looks like:

```
Source: the built-in clip  (7.1s of audio)
Word for word: Um so I think we should ah move the team meeting to Thursday at three and ...
Wispr's text:  I think we should move the team meeting to Thursday at 3 and remind ...
Answered by bt-deployment-... using canto_1.0.1, 0.91s round trip.
```

The two texts differing is proof the audio went through Wispr's own formatting pass
(fillers stripped, punctuation and numbers cleaned up).

---

## 3. Wiring another app (e.g. a phone/Safari essay or notes app) as a drop-in STT replacement

If you have another web app in this workspace (for example, an essay writing tool you open
in Safari on your phone) that currently uses Safari's built-in Web Speech API
(`SpeechRecognition`), replace the speech-to-text layer in two small pieces:

### A. In the phone/browser frontend (hardened for iOS Safari + desktop)

Replace `SpeechRecognition` with `MediaRecorder` and POST the recorded audio to **your
app's own backend** (same origin -- no CORS issues and no extra phone auth).

Six iOS Safari details are built into this snippet (and into `assets/app.js`) -- keep them
when integrating into another app:

1. **HTTPS required:** `navigator.mediaDevices` is `undefined` on plain HTTP on iOS. Open
   the app via its Imbue Studio share URL (which is HTTPS).
2. **Dynamic MIME probe + read real type on stop:** iOS Safari records `audio/mp4` (not
   `webm`), and often leaves `recorder.mimeType` empty until `dataavailable` fires. Always
   read `(chunks[0] && chunks[0].type) || recorder.mimeType || wanted || "audio/mp4"` in
   `onstop`.
3. **No timeslice in `recorder.start()`:** passing a timeslice on iOS Safari can split MP4
   into non-concatenatable fragments. Call `recorder.start()` with no arguments so `stop()`
   yields one clean MP4 blob.
4. **Screen Wake Lock while recording:** iOS Safari 16.4+ supports `navigator.wakeLock`,
   keeping the screen from auto-locking mid-paragraph during long dictation.
5. **Handle `track.onended`:** if iOS interrupts the mic (incoming call, screen lock, app
   switch), stop the recorder cleanly so the captured speech is still sent rather than lost.
6. **Use `form.append("audio", blob, filename)`** (rather than `new File(...)`) and set
   `touch-action: manipulation` on the mic button to prevent iOS double-tap zoom.

```js
function pickMime() {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return "";
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4;codecs=mp4a.40.2", "audio/mp4", "audio/aac"]) {
    if (MediaRecorder.isTypeSupported(t)) return t;
  }
  return "";
}

const wanted = pickMime();
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
const recorder = new MediaRecorder(stream, wanted ? { mimeType: wanted } : undefined);
const chunks = [];
let wakeLock = null;
try { wakeLock = await navigator.wakeLock?.request("screen"); } catch {}

recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
recorder.onstop = async () => {
  stream.getTracks().forEach((t) => t.stop());
  wakeLock?.release().catch(() => {});
  const actualType = (chunks[0] && chunks[0].type) || recorder.mimeType || wanted || "audio/mp4";
  const ext = actualType.includes("mp4") || actualType.includes("aac") ? "m4a"
    : actualType.includes("ogg") ? "ogg" : "webm";
  const form = new FormData();
  form.append("audio", new Blob(chunks, { type: actualType }), `dictation.${ext}`);
  const res = await fetch("/api/dictate", { method: "POST", body: form });
  const data = await res.json();
  // data.formatted (or data.text) has punctuated, filler-free Wispr text
};
stream.getAudioTracks().forEach((t) => {
  t.onended = () => { if (recorder.state !== "inactive") recorder.stop(); };
});
recorder.start(); // no timeslice -- keeps iOS Safari MP4 as a single valid container
```

### B. In your app's backend (either call `127.0.0.1:8122` or embed `wispr_transcriber` directly)

Either forward the uploaded audio to Wispr Transcriber's local HTTP server, or copy/embed
`audio.py`, `mac_link.py`, and `flow_client.py` directly into your app's own backend.

**Option 1 — Native endpoint (`POST /api/transcribe`):**

```python
import requests  # or httpx / urllib

# Returns {"formatted": "...", "raw": "...", "audio_seconds": ..., "round_trip_seconds": ...}
resp = requests.post(
    "http://127.0.0.1:8122/api/transcribe",
    files={"audio": (upload.filename or "dictation.m4a", audio_bytes, upload.mimetype)},
    timeout=90,
)
resp.raise_for_status()
text = resp.json()["formatted"]
```

**Option 2 — OpenAI Whisper drop-in (`POST /v1/audio/transcriptions`):**

```bash
curl -s http://127.0.0.1:8122/v1/audio/transcriptions \
  -F "file=@dictation.m4a" \
  -F "response_format=json"
# -> {"text": "..."}
```

Also supports `response_format=text` (plain text) and `response_format=verbose_json`
(full summary including both `text` and `raw`). Any client library pointing its base URL
at `http://127.0.0.1:8122/v1` works directly with no API key.

**Server-side iOS M4A note:**
- `audio.py` decodes via a seekable temp file (never `ffmpeg -i pipe:0`), because iOS
  Safari and Apple Voice Memos put the MP4 `moov` atom at the end of the file, which fails
  on an unseekable pipe once the clip exceeds ffmpeg's probe buffer (~10-15s).
- Single-stream ceiling is 4 minutes (`MAX_AUDIO_SECONDS = 240`). If a user speaks longer
  than ~3.5 minutes, stop-and-send in chunks and append the returned text into the editor.

---

## 4. How it works

```
audio file (m4a, mp4, webm, ogg, mp3, wav, ...)
   -> ffmpeg (seekable temp file)  convert to 16 kHz mono 16-bit PCM
   -> gRPC stream                  flow_api.v1.TranscriptionService/TranscribeStream
      to inference.wisprflow.com:443
      authorization: Bearer <token from session.json>
   -> two texts back               word-for-word, then Wispr's cleaned-up version
```

Three details matter, and all three are easy to get wrong:

- **Use the streaming call, not the one-shot call.** On `inference.wisprflow.com` the
  one-shot `Transcribe` answers `UNIMPLEMENTED: only transcribe_stream is supported`.
- **Send only the user's bearer token.** The desktop app also sends a key of its own
  (`baseten-authorization`) meant for a different host (`model-<id>.grpc.api.baseten.co`).
  `inference.wisprflow.com` does not want that key at all, so nothing has to be re-read
  out of the app bundle when Wispr ships an update.
- **One pass returns both texts.** Early stream messages carry the word-for-word
  transcript; the cleaned-up version appears a moment later; a final result message closes
  the stream.

The edge host `inference.wisprflow.com` is fetched by the desktop app from
`https://inference-info.wisprflow.com/dictation_url.txt`. If Wispr ever changes that host,
re-read it from that URL.

---

## 5. Token lifetime and when the Mac is needed

**Transcribing never touches the Mac.** The Mac is only needed to keep `session.json` fresh.

| Situation | Mac needed | Why |
| --- | --- | --- |
| Transcribing audio | No | The stored token is all the service wants |
| First-time setup | Yes | To share `~/Library/Application Support/Wispr Flow` |
| Stored sign-in has under 12 hours left | Only if not synced | `access_token()` re-reads the synced folder locally, or fetches over file sharing |
| Wispr replies "turned down the sign-in" | Yes | Open Wispr Flow on the Mac so it renews `session.json`, then refresh |

The token lasts about **5.5 days** in practice. **Never try to renew it from this
workspace.** Supabase rotates refresh tokens on use, so renewing here would invalidate the
desktop app's copy and could sign you out of Wispr Flow on the Mac. Always re-read
`session.json` that the desktop app itself keeps alive.

---

## 6. Troubleshooting

| What you see | What it means | What to do |
| --- | --- | --- |
| `No access to Wispr Flow's folder on your Mac is set up yet` | No file-sharing grant or synced folder found | Run Step 1 (`file-sharing` READ on `~/Library/Application Support/Wispr Flow`) |
| `Could not read Wispr Flow's session.json from your Mac` | Mac asleep, Studio closed, or Wispr signed out | Wake Mac, open Imbue Studio and Wispr Flow, then `wispr-mac-link refresh` |
| `Wispr Flow turned down the sign-in` | Token expired or revoked | Open Wispr Flow on the Mac so it refreshes `session.json`, then `wispr-mac-link refresh` |
| `The stored Wispr Flow sign-in has expired` | Nothing refreshed it for ~5 days | Same as above |
| `ffmpeg is not installed` | Fresh container | `apt-get install -y --no-install-recommends ffmpeg` |
| `That file could not be read as audio` | Not audio, or truncated upload | Check the uploaded bytes |
| `returned no text for that audio` | Silence, or under ~0.5s | Use a longer spoken clip |

---

## 7. The web page

The `wispr-transcriber` app runs on port `8122`. Open it from the workspace launcher, or
`uv run --no-sync workspace-layout open wispr-transcriber`.

- **Press the button and speak.** A ring breathes with your voice while it listens; press
  again and the recording goes straight to Wispr. Works in both desktop browsers and iOS
  Safari (via the app's share URL).
- **Drop or pick any recording**, or click **the built-in clip** to test end to end with no
  microphone.
- **Side-by-side result**: word-for-word transcript on the left (dropped fillers struck
  through), Wispr's cleaned-up version on the right (rewritten words highlighted), audio
  player, round-trip stats, and a disclosure showing Wispr's full unprocessed stream reply.

All HTTP routes:
- `POST /api/transcribe` (multipart `audio` or `file`)
- `POST /v1/audio/transcriptions` (OpenAI Whisper drop-in; multipart `file`, `response_format=json|text|verbose_json`)
- `POST /api/sample`
- `POST /api/refresh-signin`
- `GET /api/status`
- `GET /api/health`
- `GET /health`
- `GET /api/runs`, `GET /api/runs/<id>/audio`, `GET /api/runs/<id>/raw`
- `POST /api/speak` (`{"text": ...}`, optional -- only active when the legacy Mac command bridge is installed)

---

## 8. Limits and risks, stated plainly

- **This is unofficial.** It reuses a credential meant for the desktop app. Wispr's own
  terms are not written with this in mind; the realistic risk is to your own Wispr account.
  Usage counts against that account as if you dictated it.
- **It depends on Wispr's internals.** A Wispr update could change the host, the message
  format, or where `session.json` is stored. Nothing here modifies the desktop app, so an
  update cannot break your Mac; it can break this client.
- **macOS desktop app required for token renewal.** If Wispr Flow on the Mac hasn't been
  opened for ~5-7 days, the token expires until you open it again.
- **Personal vocabulary is not sent yet.** The fields exist in `flow_api.proto`; filling
  them from `config.json` is a straightforward follow-up if unusual names get misspelled.

---

## 9. Optional: legacy Mac command bridge (`deskrun`)

Workspaces that already have `.agents/skills/local-print-bridge/bin/deskrun` and a `WRITE`
grant on `~/tmp` (with `~/.config/latchkey-dev-workbench/mac_home` set) continue to work
as a fallback when no direct file-sharing grant on `~/Library/Application Support/Wispr Flow`
is present. That bridge is **not needed** for normal transcription or token refresh; it is
only needed if you want the `--speak` / "let your Mac speak" demo helper.

---

## 10. Where things live

| Path | What it is |
| --- | --- |
| `src/wispr_transcriber/mac_link.py` | Reads the sign-in from synced folder, file sharing, or legacy deskrun |
| `src/wispr_transcriber/flow_client.py` | The gRPC streaming call to Wispr |
| `src/wispr_transcriber/audio.py` | Audio conversion to 16 kHz mono PCM via ffmpeg |
| `src/wispr_transcriber/flow_api.proto` | Wispr's message format, rebuilt from the app |
| `src/wispr_transcriber/runner.py` | Flask web UI + `/api/transcribe` + `/v1/audio/transcriptions` |
| `src/wispr_transcriber/cli.py` | `wispr-transcribe` CLI |
| `data/.apps/wispr-transcriber/credentials.json` | The copied sign-in (mode 600, gitignored) |
| `data/.apps/wispr-transcriber/runs/` | Saved audio, full raw Wispr replies, and summaries |
| `docs/investigation.md` | How this was worked out, what failed, what was deliberately not done |
| `docs/design.md` | Module boundaries, safety rules, disk layout, and tests |
| `test_wispr_transcriber.py` | Unit tests; none touch Wispr or the Mac |
