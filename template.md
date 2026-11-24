---
title: "Wispr Transcriber"
description: "Turn speech into text through your own Wispr Flow account, with no official API: drop-in HTTP/OpenAI speech-to-text for phone or web apps plus a recorder page."
thumbnail: "template.svg"
version: v3
format: v2
---

# Wispr Transcriber

This file is the manifest for the **Wispr Transcriber** template (slug:
`wispr-transcriber`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Turn speech into text through your own Wispr Flow account, with no official API: drop-in HTTP/OpenAI speech-to-text for phone or web apps plus a recorder page.

Wispr Flow is a dictation app: you speak, and it writes what you said, cleaned up --
fillers removed, punctuation added, numbers and names formatted. Its official API is
closed (not taking new partners). This template lets your workspace call Wispr Flow's own
transcription servers using the adopter's **own** Wispr Flow desktop sign-in token, read
safely via a single read-only Imbue Studio file-sharing grant. Nothing on the Mac is
modified, patched, or installed.

It works both as a standalone recorder page and as an always-on local speech-to-text
backend (`http://127.0.0.1:8122`) that any other app in the workspace -- including a web
app you open in Safari on your phone -- can call to replace built-in browser dictation.
It exposes both a native route (`POST /api/transcribe`) and an OpenAI Whisper-compatible
drop-in route (`POST /v1/audio/transcriptions`), plus a CLI (`wispr-transcribe`).

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/wispr_transcriber`
- `system/supervisord.conf.d/wispr-transcriber.conf`

What each path is:

- **`system/apps/wispr_transcriber`** -- the whole app. Four small modules with one job
  each: `audio.py` converts any format ffmpeg reads (including iOS Safari `audio/mp4` /
  `.m4a` and desktop `webm`) via a seekable temp file into 16 kHz mono PCM; `mac_link.py`
  reads `session.json` from the adopter's synced or shared `~/Library/Application Support/Wispr Flow`
  folder and stores a mode-600 copy; `flow_client.py` streams audio over gRPC to
  `inference.wisprflow.com:443` carrying only the adopter's bearer token; `runner.py`
  serves the recorder UI, `/api/transcribe`, and `/v1/audio/transcriptions`; `cli.py`
  provides `wispr-transcribe`. `app.toml` sets `stop_when_no_windows = false` so the HTTP
  service stays running for other apps even when its own window is closed.
- **`system/supervisord.conf.d/wispr-transcriber.conf`** -- the program entry. It syncs
  the app's own environment, registers port **8122** with `forward_port.py`, and runs the
  service.

Every transcription run is saved under `data/.apps/wispr-transcriber/` -- the exact audio
sent, Wispr's full unprocessed stream reply, and the side-by-side summary.

**The Mac is needed only to keep the sign-in fresh, never to transcribe.** The sign-in
lasts about 5.5 days; never refresh Supabase tokens from the workspace (rotating them
could sign out the Mac app). With a synchronized read-only copy of
`~/Library/Application Support/Wispr Flow`, the workspace reads the refreshed token
locally whenever the Mac app updates it, and keeps transcribing even while the Mac is
asleep.

## Recipe

This template is version `v3`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting agent must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.


ACTIVATION

- requires_permission: file-sharing / minds-file-server-read for
  `~/Library/Application Support/Wispr Flow` on the adopter's Mac, with `"sync": {"conflict": "NEWER"}`
  enabled (user-approved; the adopting agent initiates this via a latchkey permission
  request during setup -- it must not merely mention it). This is the only grant: read-only,
  no Mac helper or Terminal installer needed. Look up the Mac's `device_id` via
  `latchkey curl -s http://latchkey-self.invalid/devices`, then POST to
  `http://latchkey-self.invalid/permission-requests` with `-H "X-Latchkey-Device: <device_id>"`
  and payload `{"path": "~/Library/Application Support/Wispr Flow", "access": "READ", "sync": {"conflict": "NEWER"}}`.
  After approval, run `system/apps/wispr_transcriber/.venv/bin/wispr-mac-link refresh` and
  `system/apps/wispr_transcriber/.venv/bin/wispr-transcribe --check` to verify end to end.

There are no secrets and no LLM: the app calls no model and holds no API key. The only
credential is the adopter's own Wispr Flow sign-in, read from `session.json` on their Mac
and never typed in.

ADAPTATION

- **A Mac with Wispr Flow installed and signed in.** The only credential Wispr's servers
  accept is the one their macOS desktop app keeps in
  `~/Library/Application Support/Wispr Flow/session.json`. There is no Windows or Linux
  path.
- **Keep the synchronized copy enabled when approving (recommended).** Leaving sync on
  copies `session.json` into `~/synced_folders/` so the workspace can refresh the token
  automatically without waking the Mac. If sync is turned off, `wispr-mac-link` still
  works automatically over the read-only file server while the Mac is awake.
- **Wire your other apps (for example, a phone/Safari essay writing tool) to it.**
  `system/apps/wispr_transcriber/README.md` (Section 3) has the exact drop-in snippet:
  replace Safari's Web Speech API (`SpeechRecognition`) with `MediaRecorder` (`audio/mp4`
  on iOS Safari, `audio/webm` on desktop), POST the recording to your own app's backend
  (same origin -- no CORS or extra phone auth), and forward server-side to
  `POST http://127.0.0.1:8122/api/transcribe` (field `audio` -> `{"formatted": "...", "raw": "..."}`)
  or `POST http://127.0.0.1:8122/v1/audio/transcriptions` (OpenAI Whisper drop-in, field
  `file` -> `{"text": "..."}`).
- **Port 8122 is assumed free.** If the adopting workspace already uses it, change it in
  `system/supervisord.conf.d/wispr-transcriber.conf` and in the app's `PORT` default.
- **Personal vocabulary is not sent yet.** The desktop app passes the user's own
  dictionary along with the audio, and this client does not, so an unusual name can come
  back misspelled. The fields exist in `flow_api.proto`; filling them from `config.json`
  is a small follow-up if needed.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

- **ffmpeg**: every piece of audio is converted to the one format Wispr's servers accept
  (16 kHz mono 16-bit PCM), and ffmpeg reads iOS Safari's `audio/mp4`/`.m4a`, desktop
  `webm`, `mp3`, `ogg`, or `wav` and resamples properly. Without it the app starts but
  refuses every recording with a clear message.

Nothing else. The app's Python dependencies come from its own lock file, which the program
syncs on start.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new agent. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (their Mac's Wispr Flow folder).
   If YES: ACTIVATE FIRST -- initiate the read-only `file-sharing` permission request
   for `~/Library/Application Support/Wispr Flow` (with sync enabled) NOW via a latchkey
   permission request (see the `connect-external-service` and `file-sharing` skills), wait
   for approval, run `wispr-mac-link refresh` and `wispr-transcribe --check`, confirm
   `http://127.0.0.1:8122/api/health` returns `"ok": true`, and open the app or test it
   live.
4. Only AFTER that ask: "How do you want to adapt it?" (for example, wiring an existing
   writing or notes app to `http://127.0.0.1:8122/api/transcribe` or
   `/v1/audio/transcriptions` to replace Safari speech-to-text).
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-11-04) -- first publication: the app, the command line, the Mac bridge, and the setup and investigation documents.

### v2 (2026-11-24) -- read-only Mac file-sharing setup (no Mac bridge needed), iOS Safari M4A fix, always-on service, OpenAI-compatible /v1/audio/transcriptions endpoint, and drop-in STT setup instructions.

### v3 (2026-11-24) -- iOS Safari recording hardening (MP4 MIME detection on stop, Screen Wake Lock, mic-interrupt recovery, safe AudioContext meter, 1-tap copy, byte-range audio test, and iOS integration guide).

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
