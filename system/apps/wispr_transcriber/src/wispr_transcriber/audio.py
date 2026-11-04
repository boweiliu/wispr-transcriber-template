"""Turn whatever audio arrives into the one format Wispr Flow's servers accept."""

import shutil
import subprocess

# What the desktop app sends, and the only thing the service reads: raw 16 kHz mono
# 16-bit little-endian samples, with no container around them.
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2
CHANNELS = 1


class AudioError(RuntimeError):
    """The audio could not be read or converted."""


def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if not found:
        raise AudioError(
            "ffmpeg is not installed in this workspace, so audio cannot be converted. "
            "Install it with: apt-get install -y --no-install-recommends ffmpeg"
        )
    return found


def to_pcm(data: bytes) -> bytes:
    """Decode any audio ffmpeg understands (m4a, mp3, wav, ogg, ...) into 16 kHz mono PCM.

    ffmpeg resamples properly, which matters: naive downsampling of a 44.1 kHz recording
    aliases and costs real accuracy in the transcript.
    """
    if not data:
        raise AudioError("The audio file was empty.")
    proc = subprocess.run(
        [
            ffmpeg_path(), "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "-acodec", "pcm_s16le",
            "pipe:1",
        ],
        input=data,
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode(errors="replace").strip().splitlines()
        raise AudioError(
            "That file could not be read as audio. " + (detail[-1][:200] if detail else "")
        )
    return proc.stdout


def duration_seconds(pcm: bytes) -> float:
    return len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)


def to_wav(pcm: bytes) -> bytes:
    """Wrap raw PCM in a WAV header, so a browser can play back exactly what was sent."""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(CHANNELS)
        handle.setsampwidth(SAMPLE_WIDTH)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()
