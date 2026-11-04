<p align="center">
  <img alt="Wispr Transcriber" src="template.svg" width="480">
</p>

# Wispr Transcriber

<p align="center">
  <a href="https://studio.imbue.com/open?git_url=https://github.com/boweiliu/wispr-transcriber-template"><img alt="Open in Imbue Studio" height="64" src="https://img.shields.io/badge/Open%20in%20Imbue%20Studio-D8D1C0?style=for-the-badge"></a>
</p>

Didn't work? Create a Studio workspace and paste this to your agent:
` /use-template https://github.com/boweiliu/wispr-transcriber-template`

## Why you care

Turn speech into text through your own Wispr Flow account, with no official API: press record, and see both the word-for-word transcript and Wispr's cleaned-up version.

Wispr Flow is very good at turning speech into clean writing — it strips the "um"s, fixes
the numbers, and keeps your tone. What it does not have is a door you can walk through:
their API is closed, and their docs say they are not taking new partners.

This gives your workspace that door, using **your own Wispr Flow account**. Dictate into a
page, hand it a voice memo, or call it from a script, and get back text as good as the one
their app writes into your editor. Nothing on your Mac is touched — no patching, no
re-signing, no settings changed. If you already pay for Wispr Flow, this is the rest of
what you are paying for.

## How to use it

1. **Say yes when your agent asks for access to a folder on your Mac.** One grant, on
   `~/tmp`. It is how your Wispr Flow sign-in gets across.
2. **Double-click one file on your Mac.** Your agent puts it there and tells you where; a
   Terminal window opens, installs a small helper, and says it is running. Once only.
3. **Open the app and press record.** Speak, press again, and your words come back as text.

You need a Mac with Wispr Flow installed and signed in — that account is what does the
work. After setup, transcribing does not need your Mac at all; it only has to be awake
about once a week so the sign-in stays fresh, which happens anyway if you use Wispr Flow.

## Ideas for making it yours

- **Dictate straight into your notes.** The app has a plain HTTP interface, so another app
  or agent in your workspace can hand it audio and get text back.
- **Transcribe a pile of recordings at once.** `wispr-transcribe <file>` works on any
  format, so a folder of voice memos becomes a folder of text with a one-line loop.
- **Send your own vocabulary.** The desktop app passes your personal dictionary along with
  the audio and this one does not, so unusual names can come back misspelled. The fields
  are already in the included message format, waiting to be filled in.
- **Record from the Mac's own microphone.** ffmpeg is usually already there; it needs a
  microphone permission for the helper, which is a decision worth making deliberately.
- **Keep a searchable record.** Every run already saves its audio and the full reply, so a
  search page over your own dictation history is a small addition.

## What this is

This repository is a published **Imbue Studio template**: a clean, bootable
snapshot of what an agent built, ready to adapt into your own. It is NOT the
generic workspace template -- it is this specific project.

[`template.md`](template.md) is the full manifest -- what it is, how it
works, what it needs to run, and what to adapt -- with the
machine-readable half (recipe, requirements, and the environment it needs
installed) in [`template.toml`](template.toml).
