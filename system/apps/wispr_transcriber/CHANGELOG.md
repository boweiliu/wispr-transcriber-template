# Changelog

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
