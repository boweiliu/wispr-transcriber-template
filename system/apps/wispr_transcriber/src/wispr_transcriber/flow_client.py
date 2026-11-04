"""Call Wispr Flow's transcription service the way its desktop app does.

The service is gRPC: ``flow_api.v1.TranscriptionService``. Two details were learned by
reading the desktop app and are easy to get wrong:

* Use ``TranscribeStream``, not ``Transcribe``. On the host the app actually dials,
  ``inference.wisprflow.com``, the one-shot call answers UNIMPLEMENTED.
* Send only the user's own bearer token. The app also sends a key of its own that it
  carries for a different host; this host does not want it, and leaving it out means
  nothing has to be re-read out of the app bundle when Wispr ships an update.

One pass returns both texts. Early messages carry the word-for-word transcript, then the
cleaned-up version appears, then a final result message closes the stream. The gap between
the two is Wispr's own formatting pass, which is the thing that makes this Wispr rather
than any other speech-to-text service.
"""

import time
import uuid
from dataclasses import dataclass, field

import grpc

from wispr_transcriber import flow_api_pb2 as pb
from wispr_transcriber import flow_api_pb2_grpc as pb_grpc

# The host the desktop app looks up at https://inference-info.wisprflow.com/dictation_url.txt
EDGE_HOST = "inference.wisprflow.com:443"
# How the app chops audio for the stream.
CHUNK_MS = 100
# What the app reports itself as. Sent as-is so the request looks like what it is.
CLIENT_NAME = "Wispr Flow"
CLIENT_VERSION = (1, 6, 1376)
UNKNOWN_UUID = "00000000-0000-0000-0000-000000000000"
SAMPLE_BYTES_PER_MS = 16000 * 2 // 1000


@dataclass
class Transcript:
    """Everything one call returned, raw response included."""

    formatted: str = ""
    raw: str = ""
    status: str = ""
    origin: str = ""
    server: str = ""
    audio_seconds: float = 0.0
    round_trip_seconds: float = 0.0
    server_total_seconds: float = 0.0
    messages: int = 0
    responses: list[dict] = field(default_factory=list)


class FlowError(RuntimeError):
    """Wispr Flow's service refused or failed the request."""


def _duration_seconds(value) -> float:
    if not value:
        return 0.0
    return float(getattr(value, "seconds", 0)) + getattr(value, "nanos", 0) / 1e9


def _build_init(token_owner: dict) -> pb.Init:
    init = pb.Init()
    meta = init.metadata
    meta.user_id = token_owner.get("user_uuid") or UNKNOWN_UUID
    meta.session_id = token_owner.get("session_id") or UNKNOWN_UUID
    meta.request_id = str(uuid.uuid4())
    meta.audio_encoding = pb.AUDIO_ENCODING_WAV
    meta.environment = pb.ENVIRONMENT_PRODUCTION
    meta.client.name = CLIENT_NAME
    meta.client.platform = pb.PLATFORM_MACOS
    meta.client.version.major, meta.client.version.minor, meta.client.version.patch = CLIENT_VERSION
    return init


def _messages(pcm: bytes, token_owner: dict, app_name: str, chunk_ms: int):
    """The stream the app sends: init + context + audio, one chunk per message, then commit."""
    chunk = SAMPLE_BYTES_PER_MS * chunk_ms
    chunks = [pcm[i:i + chunk] for i in range(0, len(pcm), chunk)] or [b""]

    first = pb.Request()
    first.init.CopyFrom(_build_init(token_owner))
    first.context.app.name = app_name
    first.context.app.type = pb.APP_TYPE_OTHER
    first.payload.audio_packets.items.append(chunks[0])
    yield first

    for item in chunks[1:]:
        message = pb.Request()
        message.payload.audio_packets.items.append(item)
        yield message

    yield pb.Request(commit=pb.COMMIT_TRUE)


def transcribe(
    pcm: bytes,
    access_token: str,
    *,
    token_owner: dict | None = None,
    raw_only: bool = False,
    app_name: str = "Workspace",
    chunk_ms: int = CHUNK_MS,
    host: str = EDGE_HOST,
    timeout: int = 90,
    keep_responses: bool = True,
) -> Transcript:
    """Send audio to Wispr Flow and collect what comes back."""
    from google.protobuf import json_format

    metadata = [("authorization", f"Bearer {access_token}")]
    if raw_only:
        metadata.append(("disable-formatting", "true"))

    result = Transcript()
    started = time.monotonic()
    channel = grpc.secure_channel(host, grpc.ssl_channel_credentials())
    try:
        stub = pb_grpc.TranscriptionServiceStub(channel)
        stream = stub.TranscribeStream(
            _messages(pcm, token_owner or {}, app_name, chunk_ms), metadata=metadata, timeout=timeout
        )
        for response in stream:
            result.messages += 1
            if keep_responses:
                result.responses.append(json_format.MessageToDict(response))
            if response.HasField("state"):
                state = response.state
                if state.raw_text.content:
                    result.raw = state.raw_text.content
                if state.formatted_text.content:
                    result.formatted = state.formatted_text.content
            if response.HasField("result"):
                final = response.result
                result.formatted = final.output.plaintext or result.formatted
                result.status = pb.ResultStatus.Name(final.status)
                result.origin = final.transcript_origin
                result.server = final.server_address
                result.audio_seconds = _duration_seconds(final.audio_duration)
                result.server_total_seconds = _duration_seconds(final.response_time.total)
                break
    except grpc.RpcError as error:
        code = error.code()
        detail = error.details() or ""
        if code is grpc.StatusCode.PERMISSION_DENIED or "Authentication" in detail:
            raise FlowError(
                "Wispr Flow turned down the sign-in. Open Wispr Flow on your Mac, then refresh "
                "the sign-in here."
            ) from error
        raise FlowError(f"Wispr Flow's service failed the request ({code.name}: {detail[:200]})") from error
    finally:
        channel.close()
        result.round_trip_seconds = time.monotonic() - started

    if not result.formatted and not result.raw:
        raise FlowError("Wispr Flow returned no text for that audio. It may be silent or too short.")
    return result
