# How the pieces fit

Four modules, each with one job, so the risky part stays small and testable.

```
cli.py / runner.py   the two front doors: a command, and a web page
    |
    +-- audio.py     any audio in, 16 kHz mono PCM out, via ffmpeg
    +-- mac_link.py  the sign-in: read it from the Mac, keep it, know when it is stale
    +-- flow_client.py   the one call that leaves the machine
```

- **`audio.py`** shells out to ffmpeg. It never trusts the input: an unreadable file raises
  `AudioError` with a sentence the page can show. ffmpeg also resamples properly, which
  matters — naive downsampling of a 44.1 kHz recording aliases and costs real accuracy.
- **`mac_link.py`** owns everything to do with the Mac. `status()` answers "is there a usable
  sign-in, and is the Mac needed", which is the only question the rest of the app asks.
  `refresh()` is the one function that reaches out.
- **`flow_client.py`** holds the gRPC call and the knowledge of Wispr's stream shape. It
  turns every gRPC failure into a `FlowError` carrying a sentence a person can act on.
- **`runner.py`** is Flask, threaded, serving at `/`. It owns the word diff and the saving.

## Safety rules built in

- **The token is read, never renewed.** See `docs/investigation.md`; renewing from here could
  sign the user out of Wispr Flow on their Mac.
- **The token is handed over as a file, not printed.** The Mac bridge leaves every command's
  output behind in `cmd_done/` on the Mac. `refresh()` writes the credentials to a scratch
  file, fetches it, and deletes it, so the token never lands in that log.
- **The stored copy is mode 600**, written with `os.open(..., 0o600)` rather than chmod after
  the fact, so it is never briefly world-readable.
- **Caps before anything is sent:** 40 MB upload, 4 minutes of audio, 400 characters for a
  Mac-spoken line, and the page stops recording at 230 s. A clip under 0.3 s is refused
  without calling Wispr at all.
- **Nothing is sent anywhere but Wispr.** There is no analytics, no third party, and the
  page's content rules admit only the app's own origin.

## What is on disk

Everything under `DATA_DIR` (`data/.apps/wispr-transcriber/`, overridable with
`WISPR_TRANSCRIBER_DATA_DIR`):

```
credentials.json        the copied sign-in, mode 600
runs/<uuid>/audio.wav   exactly the audio that was sent, 16 kHz mono
runs/<uuid>/responses.json   Wispr's replies in full, unprocessed
runs/<uuid>/summary.json     the derived view the page draws
```

The raw audio and the full replies are kept deliberately: the page's two-column view is a
*derivation*, and a later change to how it is drawn must never need the audio sent to Wispr
again. Both are reachable from the page — the audio in a player, the replies behind a
disclosure — so the derived view never stands between the user and what actually happened.

## Regenerating the message format

`flow_api.proto` was rebuilt by reading the desktop app's JavaScript bundle. If Wispr
changes the format, re-run the two extract scripts (kept in the workspace at
`data/.tasks/wispr-flow-api/`) against a newer bundle, then:

```bash
uv run --with grpcio-tools python -m grpc_tools.protoc \
    -Isrc --python_out=src --grpc_python_out=src src/wispr_transcriber/flow_api.proto
```

Generating into `src` (not the package directory) is what makes the generated gRPC module
import `from wispr_transcriber import flow_api_pb2`, which is what lets it work inside a
package.

## Tests

`test_wispr_transcriber.py` at the repo root. Nothing in it touches Wispr or the Mac: the
one outward call is behind `flow_client.transcribe`, and tests that need a result hand in a
fake. Run them in the app's own environment:

```bash
uv sync --extra test
.venv/bin/python -m pytest -q
```

The live paths are checked by hand instead, with the verification ladder in the README
(`wispr-transcribe --check` is a real clip against the real servers).
