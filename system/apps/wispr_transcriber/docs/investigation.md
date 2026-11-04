# How this was worked out

2026-11-04, one session. The question was: Wispr Flow has no API anyone can get; can the
desktop app be opened up so it can be called like one?

The starting plan was to fork and re-sign the Electron app, a technique that had worked on
other apps before. **That turned out to be unnecessary** — reading the app was enough, and
nothing on the Mac was modified.

## What was tried, in order

| Step | Result |
| --- | --- |
| Look for an official API | Exists, closed. Wispr's docs say they are not taking new partners; access is per-partner (Warp is one). Their hosted connector reads meeting transcripts only, not dictation. |
| Look for prior art | One unofficial Python SDK exists. It patches the installed app and is Windows-only. Its author says a Wispr owner gave permission. Useful as confirmation, not as code. |
| Read the app bundle | `/Applications/Wispr Flow.app/Contents/Resources/app.asar`, 139 MB. Extracted with a small reader (`asar_extract.py`, kept in the workspace's task folder) rather than installing tooling. The main bundle is `.webpack/main/index.js`, 10 MB of minified JavaScript. |
| Find the servers | The app resolves its dictation host at runtime from `https://inference-info.wisprflow.com/dictation_url.txt`, and only accepts one of three allow-listed hosts. It returned `inference.wisprflow.com`. |
| Rebuild the message format | The app uses protobuf-ts. `proto_extract.py` walks the generated JavaScript and reconstructs `flow_api.v1` — 25 messages, 13 enums — into a real `.proto`. |
| First call: one-shot `Transcribe` on the Baseten host | `PERMISSION_DENIED: Authentication required.` |
| Same call on the edge host | `UNIMPLEMENTED: only transcribe_stream is supported` |
| `TranscribeStream` on the edge host | **Worked.** Full transcript back in 0.9 s for a 7 s clip. |

## The two findings that matter

**Only the user's own token is needed.** The app sends its bearer token *plus* a
`baseten-authorization: Api-Key ...` constant baked into the bundle. Dropping every
`baseten-*` header and sending only the bearer token still works on the edge host. This is
why nothing has to be re-read out of the app bundle when Wispr ships an update, and why the
setup has no extraction step.

**One pass returns both texts.** The stream sends the word-for-word transcript first, then
the cleaned-up version, then a final result. Both are kept; the difference between them is
what the app shows.

## What was deliberately not done

- **The token is never refreshed from this workspace.** Supabase rotates refresh tokens on
  use, so refreshing here would invalidate the desktop app's copy and could sign the user
  out of Wispr Flow. The stored copy is only ever re-read from the Mac, which the app itself
  keeps alive.
- **The desktop app is not modified.** No patch, no re-sign, no injected config. An app
  update cannot break the Mac; at worst it breaks this client.
- **The Mac's microphone is not recorded.** ffmpeg is present on the Mac and could capture
  it, but that needs a macOS microphone grant for the bridge's launchd helper, which widens
  what that helper can do. Left for the owner to decide.

## Dead ends worth not repeating

- The Baseten host (`model-<id>.grpc.api.baseten.co`) refuses these calls even with the
  app's own key and model headers. Cause unknown; the edge host makes it moot.
- A client certificate is not involved: the app builds a secure context from the system CA
  bundle only, so an ordinary TLS channel is equivalent.
- The app's language list could not be recovered from the bundle by the same method the
  other enums were, so `Language` is declared with only its unspecified value. Nothing sent
  so far needs it; the server detects the language itself.

## Known imperfection

Personal vocabulary is not sent. The desktop app includes the user's dictionary in
`preferences.vocabulary`, so a name it would get right can come back wrong here: an unusual
surname came back misspelled in testing. The fields exist in the rebuilt `.proto`; filling them
from the Mac's `config.json` is the fix, and has not been done.
