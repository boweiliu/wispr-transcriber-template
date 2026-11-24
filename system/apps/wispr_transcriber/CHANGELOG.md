# Changelog

## 2026-11-24 — iOS Safari recording, playback, and copy hardening (v3)

- Hardened iOS Safari recording in `app.js`: read actual MIME type from `chunks[0].type`
  on stop (Safari often leaves `MediaRecorder.mimeType` blank until `dataavailable`), probe
  `audio/mp4;codecs=mp4a.40.2` / `audio/mp4` / `audio/aac`, omit timeslice for single-file
  MP4 integrity, acquire Screen Wake Lock while recording so the phone does not lock mid-
  dictation, stop cleanly on `track.onended` if iOS interrupts the mic, guard `AudioContext`
  / `webkitAudioContext` so the volume meter can never break recording, and upload blobs
  directly via `FormData.append`.
- Added 1-tap Copy button on the formatted transcript, `playsinline` on `<audio>`, and
  `touch-action: manipulation` on mobile buttons.
- Expanded Section 3 of the README with all 6 iOS Safari integration gotchas for agents
  embedding Wispr into another voice app.

## 2026-11-24 — read-only file-sharing setup, iOS Safari M4A fix, and drop-in STT API

- Read Wispr Flow's `session.json` directly through a single read-only Imbue Studio
  file-sharing grant on `~/Library/Application Support/Wispr Flow` (synced folder first,
  then direct WebDAV read), eliminating the need to install `local-print-bridge` /
  `deskrun` on the Mac. Existing `deskrun` workspaces still work as a fallback.
- Decode audio via a seekable temporary file in `audio.to_pcm()` so non-fragmented
  MP4/M4A recordings from iOS Safari and Voice Memos (trailing `moov` atom) convert
  cleanly at any length. Updated browser recording to support `audio/mp4` with `.m4a`
  filenames.
- Added an OpenAI Whisper-compatible `POST /v1/audio/transcriptions` endpoint (`json`,
  `text`, and `verbose_json` formats) alongside `POST /api/transcribe`.
- Changed `app.toml` to `stop_when_no_windows = false` so the HTTP service stays running
  when other workspace apps (such as a phone essay or notes tool) call it in the
  background.
- Hid the optional "let your Mac speak" control in the web UI unless `deskrun` is present,
  and added an in-page sign-in refresh button when credentials are missing or expired.
- Rewrote the README with step-by-step setup and drop-in Safari/phone STT wiring
  instructions for adopting agents.

## 2026-11-04 — first version

- Transcribe speech through your own Wispr Flow account, from inside an Imbue Studio
  workspace. Nothing on the Mac is modified.
- A page with a record button: press, speak, press again, and see what Wispr sends back.
  A ring around the button moves with your voice while it listens.
- Three other ways in: drop a recording of any format anywhere on the page, use the
  built-in clip, or have the Mac speak a line out loud.
- Both texts are shown side by side — the word-for-word transcript and Wispr's cleaned-up
  version — with the fillers it dropped struck through and rewritten words highlighted.
- Every run keeps its audio and Wispr's full reply, both reachable from the page.
- A command line too: `wispr-transcribe <file>`, `--check`, `--speak`.
- `wispr-mac-link status` / `refresh` manage the sign-in copied from the Mac. The app
  refreshes it by itself when it is within 12 hours of running out.
- Confirmed working with Wispr Flow 1.6.1376 on macOS 15.6.
