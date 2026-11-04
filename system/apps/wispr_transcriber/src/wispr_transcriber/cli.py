"""Transcribe an audio file from the command line, and verify the whole path works.

    wispr-transcribe recording.m4a          # any format ffmpeg reads
    wispr-transcribe --check                # end to end on a built-in clip
    wispr-transcribe --speak "hello there"  # have the Mac say it, then transcribe that
"""

import argparse
import json
import sys
from importlib import resources
from pathlib import Path

from wispr_transcriber import audio, flow_client, mac_link


def sample_bytes() -> bytes:
    return (resources.files("wispr_transcriber") / "assets" / "sample.wav").read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(description="Turn speech into text through your Wispr Flow account.")
    parser.add_argument("path", nargs="?", help="an audio file in any format ffmpeg reads")
    parser.add_argument("--check", action="store_true", help="transcribe the built-in clip and report")
    parser.add_argument("--speak", metavar="TEXT", help="have the Mac speak TEXT, then transcribe it")
    parser.add_argument("--raw", action="store_true", help="skip Wispr's clean-up, word for word only")
    parser.add_argument("--json", action="store_true", help="print the whole response")
    parser.add_argument("--no-refresh", action="store_true", help="never reach for the Mac")
    args = parser.parse_args()

    sources = [bool(args.path), args.check, bool(args.speak)]
    if sum(1 for flag in sources if flag) != 1:
        parser.error("give one of: an audio file, --check, or --speak TEXT")

    try:
        if args.check:
            data = sample_bytes()
            label = "the built-in clip"
        elif args.speak:
            data = mac_link.say(args.speak)
            label = f"your Mac saying {args.speak!r}"
        else:
            data = Path(args.path).read_bytes()
            label = args.path

        pcm = audio.to_pcm(data)
        token = mac_link.access_token(auto_refresh=not args.no_refresh)
        creds = mac_link.load() or {}
        result = flow_client.transcribe(
            pcm, token, token_owner=creds, raw_only=args.raw, keep_responses=args.json
        )
    except (mac_link.MacLinkError, mac_link.CredentialsMissing, audio.AudioError, flow_client.FlowError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps({"responses": result.responses}, indent=1))
        return

    print(f"Source: {label}  ({result.audio_seconds:.1f}s of audio)")
    if result.raw and result.raw != result.formatted:
        print(f"Word for word: {result.raw}")
    print(f"Wispr's text:  {result.formatted}")
    print(
        f"Answered by {result.server or 'unknown'} using {result.origin or 'unknown'}, "
        f"{result.round_trip_seconds:.2f}s round trip."
    )


if __name__ == "__main__":
    main()
