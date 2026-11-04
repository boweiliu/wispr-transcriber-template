# Wispr Flow from this workspace

Turn speech into text using your own Wispr Flow account, from inside an Imbue Studio
workspace, without modifying the Wispr Flow desktop app.

Wispr Flow does have an official API, but it is closed: their docs say they are not taking
new partners. This goes a different way. The Wispr Flow desktop app on the Mac talks to
Wispr's own transcription servers, and those servers accept nothing but the sign-in token
the app keeps on disk. So this workspace borrows that token and makes the same calls the
app makes. Nothing is patched, re-signed, or installed on the Mac beyond the command
bridge described below.

Built and verified 2026-11-04 against Wispr Flow 1.6.1376 on macOS 15.6.

---

## 1. What you get

| Thing | Command | Needs the Mac? |
| --- | --- | --- |
| Transcribe an audio file | `wispr-transcribe recording.m4a` | No |
| Check the whole path works | `wispr-transcribe --check` | No |
| Record a line on the Mac, then transcribe it | `wispr-transcribe --speak "hello"` | Yes |
| See / replace the stored sign-in | `wispr-mac-link status` / `refresh` | `refresh` only |
| Web page (see section 8) | runs as the `wispr-transcriber` app | No |
| Python library | `from wispr_transcriber import audio, flow_client, mac_link` | No |

## 2. How it works

```
audio file (any format)
   -> ffmpeg               convert to 16 kHz mono 16-bit PCM
   -> gRPC stream          flow_api.v1.TranscriptionService/TranscribeStream
      to inference.wisprflow.com:443
      authorization: Bearer <token copied from the Mac>
   -> two texts back       word-for-word, then Wispr's cleaned-up version
```

Three details matter, and all three are easy to get wrong:

- **Use the streaming call, not the one-shot call.** On `inference.wisprflow.com` the
  one-shot `Transcribe` answers `UNIMPLEMENTED: only transcribe_stream is supported`.
- **Send only the user's bearer token.** The desktop app also sends a key of its own
  (`baseten-authorization`) meant for a different host, `model-<id>.grpc.api.baseten.co`.
  That host rejected our calls with `PERMISSION_DENIED: Authentication required.`, and
  `inference.wisprflow.com` does not want the key at all. Leaving it out means nothing has
  to be re-read out of the app bundle when Wispr ships an update.
- **One pass returns both texts.** Early stream messages carry the word-for-word
  transcript; the cleaned-up version appears a moment later; a final result message closes
  the stream. The difference between them is Wispr's own formatting pass — the thing that
  makes this Wispr rather than generic speech-to-text.

The app address `inference.wisprflow.com` is not hardcoded by Wispr; the desktop app
fetches it from `https://inference-info.wisprflow.com/dictation_url.txt` and will only
accept one of three allow-listed hosts. If Wispr changes that value, re-read it from that
URL.

## 3. When the Mac is needed

**Transcribing never touches the Mac.** The Mac is needed only to get a sign-in, and to
record something with `--speak`.

| Situation | Mac needed | Why |
| --- | --- | --- |
| Transcribing audio | No | The stored token is all the service wants |
| First-time setup | Yes | To copy the first sign-in |
| Stored sign-in has under 12 hours left | Yes | `access_token(...)` refreshes it automatically |
| Wispr replies "turned down the sign-in" | Yes | The token was revoked or expired early |
| `--speak`, or any Mac-recorded audio | Yes | The recording happens there |

The token seen in practice lasted about 5.5 days. **Never try to renew it from this
workspace.** Supabase rotates refresh tokens on use, so renewing here would invalidate the
desktop app's copy and could sign you out of Wispr Flow on the Mac. Always re-read the
token the app already renewed instead — that is exactly what `wispr-mac-link refresh` does.

For the Mac to answer at all: it must be awake, the Imbue Studio app must be running, and
Wispr Flow must be installed and signed in. Wispr Flow should also have been opened within
the last week or so, since it is the app that keeps its own token alive.

## 4. One-time setup

Steps 1-3 are already done in this workspace. Re-do them in a fresh workspace, or when
something below reports missing.

**1. Install the command bridge.** `deskrun` runs one shell command on the Mac. It lives at
`.agents/skills/local-print-bridge/bin/deskrun`. It is assembled from two repos:

```bash
# the skill (only the skill -- the rest of that repo is an old workspace snapshot)
git fetch <gateway>/boweiliu/local-print-bridge.git main
git checkout FETCH_HEAD -- .agents/skills/local-print-bridge
# deskrun itself, then apply the per-device fix described in its SKILL.md
git fetch <gateway>/boweiliu/latchkey-dev-workbench.git main
git show FETCH_HEAD:.agents/skills/latchkey-dev-workbench/bin/deskrun \
  > .agents/skills/local-print-bridge/bin/deskrun
chmod +x .agents/skills/local-print-bridge/bin/deskrun
```

Both fetches go through the latchkey git gateway; see `.agents/skills/connect-external-service`.

**2. Point it at the Mac.**

Both values are read at runtime, so look them up rather than guessing: the home directory is
the Mac's own (`/Users/<your short username>`), and the device id comes from the devices list.

```bash
mkdir -p ~/.config/latchkey-dev-workbench
echo "/Users/$(whoami)" > ~/.config/latchkey-dev-workbench/mac_home   # the Mac's username, not this container's
latchkey curl -s http://latchkey-self.invalid/devices                 # copy the Mac's device_id
echo host-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx > ~/.config/latchkey-dev-workbench/mac_device
```

With no `mac_device` file, the tools fall back to the most recently seen device, which is
right when the account has one computer.

**3. Make sure the workspace may write to the Mac's `~/tmp`.** This is how bytes come back.
Check first; a workspace that has used the bridge before already has it:

```bash
latchkey curl -s http://latchkey-self.invalid/permissions/self \
  | grep -o 'minds-file-server-write-[^"]*tmp[^"]*'
```

If nothing prints, ask for it (the request pops a dialog on the Mac):

```bash
latchkey curl -XPOST http://latchkey-self.invalid/permission-requests \
  -H 'Content-Type: application/json' \
  -d '{"agent_id": "'"${MINDS_CHAT_ID:-$MNGR_AGENT_ID}"'", "type": "file-sharing",
       "payload": {"path": "~/tmp", "access": "WRITE"},
       "rationale": "To copy the Wispr Flow sign-in and audio between your Mac and this workspace."}'
```

**4. Install the audio converter and this tool.**

```bash
apt-get install -y --no-install-recommends ffmpeg
supervisorctl reread && supervisorctl update    # the program syncs its own environment
```

**5. Copy the first sign-in.**

```bash
wispr-mac-link refresh
```

## 5. The Mac side

Everything on the Mac runs through one small helper, already installed from earlier
sessions: a launchd agent called **`com.minds.printbridge`**. It polls
`~/tmp/minds_data/printbridge/cmd/` every 15 seconds, runs any shell snippet dropped there,
and writes the output next to it. `deskrun` just drops a snippet and waits.

**Nothing has to be run on the Mac by hand in normal use.** The agent does it all through
`deskrun`. Two cases need a human at the Mac:

**If the helper is gone** (the checks in section 6 time out, but the Mac is awake and
Studio is running), reinstall it by double-clicking a single file. The agent stages it:

```bash
# in the workspace: copy the setup file to the Mac
D=$(cat ~/.config/latchkey-dev-workbench/mac_device)
H=$(cat ~/.config/latchkey-dev-workbench/mac_home)
latchkey curl -H "X-Latchkey-Device: $D" \
  -T ".agents/skills/local-print-bridge/assets/Set Up Minds Printing.terminal" \
  "http://latchkey-self.invalid/minds-api-proxy/api/v1/files/$D$H/tmp/minds_data/Set%20Up%20Minds%20Printing.terminal"
```

Then the human, on the Mac, opens `~/tmp/minds_data/` and **double-clicks
`Set Up Minds Printing.terminal`**. A Terminal window opens, installs the helper, and
reports that it is running. It is safe to run again; it is idempotent. The file is a
`.terminal` rather than a script because the file bridge cannot set the executable bit.

**If Wispr Flow is signed out**, the human opens Wispr Flow on the Mac and signs in. Then
`wispr-mac-link refresh` here.

Useful one-off commands, all run from the workspace:

```bash
# is the bridge alive?
.agents/skills/local-print-bridge/bin/deskrun 'echo CONNECT_OK; hostname'

# is the helper loaded and running?
.agents/skills/local-print-bridge/bin/deskrun 'launchctl list | grep printbridge'

# is Wispr Flow installed, which version, and is there a saved sign-in?
.agents/skills/local-print-bridge/bin/deskrun 'defaults read "/Applications/Wispr Flow.app/Contents/Info" CFBundleShortVersionString; ls -la ~/Library/Application\ Support/Wispr\ Flow/session.json'

# is Wispr Flow running right now?
.agents/skills/local-print-bridge/bin/deskrun 'pgrep -f "Wispr Flow" >/dev/null && echo RUNNING || echo NOT RUNNING'
```

Two gotchas with the bridge: each command gets about 60 seconds, and its output is left
behind on the Mac in `~/tmp/minds_data/printbridge/cmd_done/`. Never print a token through
it — `mac_link.refresh()` deliberately hands the token over as a file and deletes it
afterwards, so it never lands in that log.

## 6. Verifying it works

Run these in order and stop at the first failure. Each is one command.

```bash
# 1. Can we reach the Mac at all?  Expect: CONNECT_OK and the hostname.
.agents/skills/local-print-bridge/bin/deskrun 'echo CONNECT_OK; hostname'

# 2. Does this workspace hold a current sign-in?  Expect: "signed_in": true, "needs_mac": false.
wispr-mac-link status

# 3. Does a real transcription work end to end?  Expect two lines of text and a server name.
wispr-transcribe --check

# 4. Does the Mac-recorded path work?  Expect the spoken sentence to come back as text.
wispr-transcribe --speak "This is a test of the connection to Wispr Flow."

# 5. Is the web app healthy?  Expect: {"ok": true, ...}
curl -s localhost:8122/api/health
```

What a good step 3 looks like:

```
Source: the built-in clip  (7.1s of audio)
Word for word: Um so I think we should ah move the team meeting to Thursday at three and ...
Wispr's text:  I think we should move the team meeting to Thursday at 3 and remind Sophie ...
Answered by bt-deployment-qvxldzj-00001-deployment-84fd66bcdc-hztgk using canto_1.0.1, 0.91s round trip.
```

The two texts differing is the proof that the audio went through Wispr specifically: the
fillers are gone, "three" became "3". `canto_1.0.1` is Wispr's own model name, and the
server name is the machine in their fleet that answered.

## 7. Troubleshooting

| What you see | What it means | What to do |
| --- | --- | --- |
| `Could not reach your Mac` | Mac asleep, or Studio app closed | Wake the Mac, open Imbue Studio, retry |
| deskrun times out but the Mac is awake | The launchd helper is gone | Reinstall it: section 5 |
| `Wispr Flow has no saved sign-in on this Mac` | Signed out of Wispr Flow | Sign in on the Mac, then `wispr-mac-link refresh` |
| `Wispr Flow turned down the sign-in` | Token expired or revoked | Open Wispr Flow on the Mac, then `wispr-mac-link refresh` |
| `The stored Wispr Flow sign-in has expired` | Nothing refreshed it for ~5 days | Same as above |
| `UNIMPLEMENTED: only transcribe_stream is supported` | Used the one-shot call | Use the streaming call (section 2) |
| `PERMISSION_DENIED: Authentication required.` | Dialling the Baseten host, not the edge host | Use `inference.wisprflow.com:443` |
| `ffmpeg is not installed` | Fresh container | `apt-get install -y --no-install-recommends ffmpeg` |
| `That file could not be read as audio` | Not an audio file, or truncated | Check the file |
| `returned no text for that audio` | Silence, or under ~0.5s | Use a longer clip |

## 8. The web page

The `wispr-transcriber` app, port 8122, registered from
`system/supervisord.conf.d/wispr-transcriber.conf`. Open it from the workspace launcher, or
`uv run --no-sync workspace-layout open wispr-transcriber`.

**Press the button and speak.** A ring around it moves with your voice while it listens;
press again and the recording goes straight to Wispr. The microphone works inside an Imbue
Studio app window (confirmed on macOS 15.6); if some client ever refuses, the page says so
and offers the app's share URL, which opens the same page as an ordinary browser tab.

Three other ways in, on one line under the button: **use a recording** (or drop a file
anywhere on the page — any format ffmpeg reads), **the built-in clip** (needs no Mac at
all), and **let your Mac speak** a line out loud.

What a result shows, and why each part is there:

| On the page | What it tells you |
| --- | --- |
| Left panel | What Wispr heard, word for word, with everything it later dropped struck through |
| Right panel | What Wispr sent back, with rewritten words highlighted |
| The line below | Audio length, round trip, how many edits Wispr made, its model (`canto_1.0.1`), and which machine in their fleet answered |
| The player | Exactly the audio that was sent, so you can hear what Wispr heard |
| "Wispr's full reply" | The unprocessed stream, as it arrived |
| The header pill | Which account is signed in and how long the sign-in has left |

The two panels differing is the proof that the audio went through Wispr specifically,
rather than through any other speech-to-text service: the gap between them is Wispr's own
formatting pass.

Routes, if you want to drive it from elsewhere: `POST /api/transcribe` (multipart `audio`),
`POST /api/sample`, `POST /api/speak` (`{"text": ...}`), `POST /api/refresh-signin`,
`GET /api/status`, `GET /api/health`, `GET /api/runs`, `GET /api/runs/<id>/audio`,
`GET /api/runs/<id>/raw`.

## 9. Limits and risks, stated plainly

- **This is unofficial.** It reuses a credential meant for the desktop app. Wispr's own
  terms are not written with this in mind; the realistic risk is to your own Wispr account.
  Usage counts against that account exactly as if he had dictated it.
- **It depends on Wispr's internals.** A Wispr update could change the host, the message
  format, or where the token is stored. Nothing here patches the app, so an update cannot
  break the Mac; it can break this client. Section 2 says where to re-read each value.
- **The sign-in is the whole dependency.** No Mac for a week means no transcription until
  the Mac is reachable again.
- **The record button works inside an Imbue Studio app window** (confirmed 2026-11-04 on
  macOS 15.6), contrary to the usual assumption that workspace windows cannot reach a
  microphone. If some other client ever refuses, the page says so and offers the app's share
  URL, which opens the same page as an ordinary browser tab. A third path exists and is not
  built: recording the Mac's own microphone through the bridge, since ffmpeg is installed at
  `/opt/homebrew/bin/ffmpeg` there. It needs a macOS microphone permission grant for the
  launchd helper, which pops a dialog and widens what that helper can do, so ask the owner first.
- **macOS only**, because the token and the bridge both are.
- **The command bridge is a standing arbitrary-code channel** into the Mac, by design: the
  helper runs whatever is dropped in its inbox, as your own user account. That predates this
  project and is documented in the `local-print-bridge` skill. Worth knowing it is what
  this depends on.

## 10. Where things live

| Path | What it is |
| --- | --- |
| `system/apps/wispr_transcriber/` | The app: client, CLI, web page |
| `system/apps/wispr_transcriber/src/wispr_transcriber/mac_link.py` | The Mac link, and the stored sign-in |
| `.../flow_client.py` | The gRPC call to Wispr |
| `.../audio.py` | Audio conversion |
| `.../flow_api.proto` | Wispr's message format, rebuilt from the app |
| `data/.apps/wispr-transcriber/credentials.json` | The copied sign-in (mode 600, gitignored) |
| `.../docs/investigation.md` | How this was worked out, what failed, what was deliberately not done |
| `.../docs/design.md` | How the modules fit, the safety rules, what is on disk |
| `.../test_wispr_transcriber.py` | Tests; none touch Wispr or the Mac |
| `.agents/skills/local-print-bridge/` | `deskrun` and the Mac helper's assets |
| `~/.config/latchkey-dev-workbench/` | Which Mac to talk to |

`flow_api.proto` was rebuilt by reading the desktop app's own JavaScript bundle. If Wispr
ever changes the format, `docs/investigation.md` says how it was recovered and
`docs/design.md` has the command to regenerate the Python from the `.proto`.
