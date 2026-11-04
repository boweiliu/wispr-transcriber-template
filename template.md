---
title: "Wispr Transcriber"
description: "Turn speech into text through your own Wispr Flow account, with no official API: press record, and see both the word-for-word transcript and Wispr's cleaned-up version."
thumbnail: "template.svg"
version: v1
format: v2
---

# Wispr Transcriber

This file is the manifest for the **Wispr Transcriber** template (slug:
`wispr-transcriber`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Turn speech into text through your own Wispr Flow account, with no official API: press record, and see both the word-for-word transcript and Wispr's cleaned-up version.

Wispr Flow is a dictation app: you speak, and it writes what you said, cleaned up — fillers
removed, numbers and names formatted. It has an official API, but it is closed; their docs
say they are not taking new partners. This template lets a workspace use it anyway, through
the adopter's **own** Wispr Flow account, by making the same calls the Wispr Flow desktop app
makes. Nothing on the Mac is modified: no patching, no re-signing, no injected config.

What the user sees is a page with one big record button. They press it, speak, press again,
and a moment later two panels appear: on the left what Wispr heard word for word, on the
right what Wispr sent back after its own clean-up, with the dropped fillers struck through
and rewritten words highlighted. Underneath are the audio they just recorded, how long the
round trip took, and which machine in Wispr's fleet answered. A file of any format can be
dropped on the page instead, and there is a built-in clip for trying it with nothing set up.
There is a command-line version too, for scripts and other agents.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/wispr_transcriber`
- `system/supervisord.conf.d/wispr-transcriber.conf`
- `.agents/skills/local-print-bridge`

What each path is:

- **`system/apps/wispr_transcriber`** — the whole app. Four small modules with one job each:
  `audio.py` converts anything ffmpeg reads into the 16 kHz mono audio Wispr wants;
  `mac_link.py` owns the connection to the Mac and the sign-in copied from it;
  `flow_client.py` makes the one call that leaves the machine; `runner.py` is the Flask
  page and `cli.py` the command. Its `docs/` carry the design and the investigation that
  produced it, and `test_wispr_transcriber.py` holds 30 tests that touch neither Wispr nor
  the Mac.
- **`system/supervisord.conf.d/wispr-transcriber.conf`** — the program entry. It syncs the
  app's own environment, registers port **8122** with `forward_port.py` so the window gets
  its own origin, then runs the app.
- **`.agents/skills/local-print-bridge`** — the channel to the Mac. Its `bin/deskrun` runs
  one shell command there, which is how the sign-in is fetched and how the Mac is asked to
  speak a line. Despite the name, this app uses it for neither printing nor anything else.

How it fits together at runtime: the page posts audio to the app; the app converts it,
reads the stored sign-in, and opens a gRPC stream to `inference.wisprflow.com` carrying
only the adopter's own bearer token. Every run is saved under
`data/.apps/wispr-transcriber/` — the audio exactly as sent, and Wispr's full reply — and
both are reachable from the page, so the two-panel view never stands between the user and
what actually happened.

**The Mac is needed only for the sign-in, never to transcribe.** The sign-in lasts about
five days and the app re-reads it by itself when it is within twelve hours of running out.

## Recipe

This template is version `v1`. It is not a fork of the
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

- requires_permission: file-sharing / minds-file-server-write for `~/tmp` on the adopter's
  Mac (user-approved; the adopting agent initiates this via a latchkey permission request
  during setup -- it must not merely mention it). This is the only grant. It is how the
  Wispr Flow sign-in and any recorded audio travel back from the Mac.

There are no secrets and no LLM: the app calls no model and holds no API key. The only
credential is the adopter's own Wispr Flow sign-in, which is copied from their Mac and
never typed in.

ADAPTATION

- **A Mac with Wispr Flow on it, signed in.** This is the whole premise and nothing in the
  template can supply it: the only credential Wispr's servers accept is the one their
  desktop app stores. The adopter needs macOS, Wispr Flow installed, and an account they
  have signed into at least once. There is no Windows or Linux path.
- **The command bridge must be installed on that Mac, and only a human can start it.** The
  `local-print-bridge` skill ships the installer, but it is a file the adopter
  double-clicks on their own machine; an agent cannot run it for them. The app's README has
  the exact steps and the check that proves it worked. Until it is installed, the sign-in
  cannot be fetched and the app has nothing to authenticate with.
- **Point the bridge at the right Mac.** The Mac's home directory and device id go in
  `~/.config/latchkey-dev-workbench/`; the app's README gives the two commands that look
  them up. With one computer on the account the device id can be left unset.
- **Port 8122 is assumed free.** If the adopting workspace already uses it, change it in
  `system/supervisord.conf.d/wispr-transcriber.conf` and in the app's `PORT` default.
- **Personal vocabulary is not sent.** The desktop app passes the user's own dictionary
  along with the audio, and this client does not, so an unusual name can come back
  misspelled. The fields exist in the included `.proto`; filling them from the Mac's
  `config.json` is the fix and has not been done.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

- **ffmpeg**: every piece of audio is converted to the one format Wispr's servers accept
  (16 kHz mono 16-bit), and ffmpeg is what reads the adopter's m4a, mp3, webm or wav and
  resamples it properly. Without it the app starts but refuses every recording with a
  message saying so.

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
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `connect-external-service` skill; the request
   opens the approval/login flow in the Imbue Studio app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
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

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
