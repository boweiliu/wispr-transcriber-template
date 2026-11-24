"""Tests for the Wispr Transcriber.

None of these touch Wispr's servers or the Mac: the one call that leaves the machine is
behind `flow_client.transcribe`, and the tests that need a result hand in a fake. Run them
from the repo root:

    PYTHONPATH=src uv run --no-sync pytest system/apps/wispr_transcriber -m '' --no-cov

The live paths are exercised by hand instead, with `wispr-transcribe --check` (real audio,
real servers) and `wispr-mac-link status` -- see the README's verification ladder.
"""

import io
import json
import subprocess
import time
import wave

import pytest

from wispr_transcriber import audio, flow_client, mac_link, runner


# ---- Audio -------------------------------------------------------------------------------


def make_wav(seconds: float = 1.0, rate: int = 44100, channels: int = 2) -> bytes:
    """A silent clip in a format a phone or laptop would actually produce."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds) * channels)
    return buffer.getvalue()


def test_any_sample_rate_and_channel_count_becomes_16k_mono() -> None:
    pcm = audio.to_pcm(make_wav(seconds=2.0, rate=44100, channels=2))
    # 2 seconds at 16 kHz mono 16-bit, whatever went in.
    assert len(pcm) == pytest.approx(2.0 * 16000 * 2, rel=0.01)
    assert audio.duration_seconds(pcm) == pytest.approx(2.0, rel=0.01)


def test_the_browsers_own_format_converts() -> None:
    """The record button sends webm/opus; if that stopped converting the app would be useless."""
    source = make_wav(seconds=1.0, rate=16000, channels=1)
    encoded = subprocess.run(
        [audio.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-c:a", "libopus", "-b:a", "32k", "-f", "webm", "pipe:1"],
        input=source, capture_output=True, check=True,
    ).stdout
    assert audio.duration_seconds(audio.to_pcm(encoded)) == pytest.approx(1.0, abs=0.1)


def test_long_m4a_with_trailing_moov_converts(tmp_path) -> None:
    """iOS Safari MediaRecorder and Voice Memos produce MP4/M4A with the moov atom at the end."""
    m4a_path = tmp_path / "safari.m4a"
    subprocess.run(
        [audio.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anoisesrc=duration=15", "-c:a", "aac", "-b:a", "128k",
         str(m4a_path)],
        check=True, capture_output=True,
    )
    pcm = audio.to_pcm(m4a_path.read_bytes())
    assert audio.duration_seconds(pcm) == pytest.approx(15.0, abs=0.2)


def test_a_file_that_is_not_audio_is_refused_in_plain_words() -> None:
    with pytest.raises(audio.AudioError) as caught:
        audio.to_pcm(b"this is not audio, it is a sentence")
    assert "could not be read as audio" in str(caught.value)


def test_empty_input_is_refused() -> None:
    with pytest.raises(audio.AudioError):
        audio.to_pcm(b"")


def test_wrapping_pcm_back_into_a_wav_keeps_the_audio() -> None:
    pcm = audio.to_pcm(make_wav(seconds=1.5, rate=16000, channels=1))
    with wave.open(io.BytesIO(audio.to_wav(pcm))) as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getnframes() == len(pcm) // 2


# ---- What Wispr changed ------------------------------------------------------------------


def test_the_diff_marks_a_dropped_filler_on_the_left_only() -> None:
    diff = runner.word_diff("Um so I think we should go", "I think we should go")
    assert [part["kind"] for part in diff["left"]] == ["dropped", "same"]
    assert [part["kind"] for part in diff["right"]] == ["same"]
    assert "Um so " in diff["left"][0]["text"]
    assert diff["edits"] == 1


def test_the_diff_marks_a_rewritten_word_on_both_sides() -> None:
    diff = runner.word_diff("meet at three", "meet at 3")
    assert diff["edits"] == 1
    assert any(part["kind"] == "changed" and "three" in part["text"] for part in diff["left"])
    assert any(part["kind"] == "changed" and "3" in part["text"] for part in diff["right"])


def test_identical_texts_report_no_edits() -> None:
    diff = runner.word_diff("nothing changed here", "nothing changed here")
    assert diff["edits"] == 0
    assert all(part["kind"] == "same" for part in diff["left"] + diff["right"])


def test_the_diff_survives_an_empty_side() -> None:
    assert runner.word_diff("", "")["edits"] == 0
    assert runner.word_diff("something", "")["edits"] == 1


# ---- The stream sent to Wispr --------------------------------------------------------------


def test_the_first_message_carries_the_setup_and_the_last_commits() -> None:
    pcm = b"\x00\x00" * 16000  # one second
    messages = list(flow_client._messages(pcm, {"user_uuid": "u", "session_id": "s"}, "App", 100))

    first = messages[0]
    assert first.init.metadata.user_id == "u"
    assert first.init.metadata.audio_encoding == flow_client.pb.AUDIO_ENCODING_WAV
    assert first.context.app.name == "App"
    assert len(first.payload.audio_packets.items) == 1

    last = messages[-1]
    assert last.commit == flow_client.pb.COMMIT_TRUE
    assert not last.payload.audio_packets.items

    # One 100 ms chunk per message between the two, and no setup repeated.
    middle = messages[1:-1]
    assert len(middle) == 9
    assert all(not message.HasField("init") for message in middle)


def test_a_missing_account_still_produces_a_valid_request() -> None:
    messages = list(flow_client._messages(b"\x00\x00" * 1600, {}, "App", 100))
    assert messages[0].init.metadata.user_id == flow_client.UNKNOWN_UUID


# ---- The stored sign-in --------------------------------------------------------------------


def write_credentials(tmp_path, monkeypatch, expires_in: float) -> None:
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({
        "access_token": "fake-token",
        "expires_at": time.time() + expires_in,
        "email": "someone@example.com",
    }))
    monkeypatch.setattr(mac_link, "CREDENTIALS_PATH", path)


def test_with_no_sign_in_the_mac_is_needed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mac_link, "CREDENTIALS_PATH", tmp_path / "absent.json")
    state = mac_link.status()
    assert state["signed_in"] is False
    assert state["needs_mac"] is True


def test_a_fresh_sign_in_needs_nothing(tmp_path, monkeypatch) -> None:
    write_credentials(tmp_path, monkeypatch, expires_in=5 * 86400)
    state = mac_link.status()
    assert state["signed_in"] is True
    assert state["needs_mac"] is False


def test_a_sign_in_about_to_run_out_asks_for_the_mac_while_still_working(tmp_path, monkeypatch) -> None:
    write_credentials(tmp_path, monkeypatch, expires_in=60)
    state = mac_link.status()
    assert state["signed_in"] is True
    assert state["needs_mac"] is True


def test_an_expired_sign_in_is_not_signed_in(tmp_path, monkeypatch) -> None:
    write_credentials(tmp_path, monkeypatch, expires_in=-60)
    state = mac_link.status()
    assert state["signed_in"] is False
    assert state["needs_mac"] is True


def test_an_expired_token_is_refused_rather_than_sent(tmp_path, monkeypatch) -> None:
    write_credentials(tmp_path, monkeypatch, expires_in=-60)
    with pytest.raises(mac_link.CredentialsMissing):
        mac_link.access_token(auto_refresh=False)


def test_a_corrupt_credentials_file_reads_as_no_sign_in(tmp_path, monkeypatch) -> None:
    path = tmp_path / "credentials.json"
    path.write_text("{ this is not json")
    monkeypatch.setattr(mac_link, "CREDENTIALS_PATH", path)
    assert mac_link.load() is None


def install_fake_deskrun(tmp_path, monkeypatch) -> None:
    """Stand in for the bridge script so the parsing can be tested with no Mac anywhere."""
    script = tmp_path / "deskrun"
    script.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(mac_link, "DESKRUN", script)


def test_deskrun_reports_a_failure_on_the_mac_rather_than_returning_its_output(tmp_path, monkeypatch) -> None:
    install_fake_deskrun(tmp_path, monkeypatch)
    monkeypatch.setattr(mac_link.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout="=== dr-1-1.cmd @ now ===\nno such file\n=== exit 2 ===\n", stderr="",
    ))
    with pytest.raises(mac_link.MacLinkError) as caught:
        mac_link.deskrun("ls /nope")
    assert "exit 2" in str(caught.value)
    assert "no such file" in str(caught.value)


def test_deskrun_returns_just_the_output_on_success(tmp_path, monkeypatch) -> None:
    install_fake_deskrun(tmp_path, monkeypatch)
    monkeypatch.setattr(mac_link.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout="=== dr-1-1.cmd @ now ===\nCONNECT_OK\n=== exit 0 ===\n", stderr="",
    ))
    assert mac_link.deskrun("echo CONNECT_OK").strip() == "CONNECT_OK"


def _sample_session_json(expires_in: int = 86400) -> str:
    inner = json.dumps({
        "access_token": "tok-example-12345",
        "expires_at": int(time.time()) + expires_in,
        "user": {"email": "adopter@example.com"},
    })
    return json.dumps({"sb-example-auth-token": inner})


def test_refresh_from_synced_folder_reads_session_and_config(tmp_path, monkeypatch) -> None:
    support = tmp_path / "synced_folders" / "host-abc" / "Users" / "alice" / "Library" / "Application Support" / "Wispr Flow"
    support.mkdir(parents=True)
    (support / "session.json").write_text(_sample_session_json())
    (support / "config.json").write_text(json.dumps({"prefs": {"user": {"uuid": "u-1"}, "sessionId": "s-1"}}))

    creds_path = tmp_path / "data" / "credentials.json"
    monkeypatch.setattr(mac_link, "DATA_DIR", creds_path.parent)
    monkeypatch.setattr(mac_link, "CREDENTIALS_PATH", creds_path)
    monkeypatch.setattr(mac_link, "_home_candidates", lambda: [tmp_path])
    monkeypatch.delenv("WISPR_FLOW_SUPPORT_DIR", raising=False)

    payload = mac_link.refresh()
    assert payload["access_token"] == "tok-example-12345"
    assert payload["email"] == "adopter@example.com"
    assert payload["user_uuid"] == "u-1"
    assert payload["session_id"] == "s-1"
    assert payload["source"] == "synced_folder"
    assert (creds_path.stat().st_mode & 0o777) == 0o600
    assert mac_link.status()["signed_in"] is True


def test_refresh_from_file_sharing_discovers_grant_and_fetches(tmp_path, monkeypatch) -> None:
    creds_path = tmp_path / "data" / "credentials.json"
    monkeypatch.setattr(mac_link, "DATA_DIR", creds_path.parent)
    monkeypatch.setattr(mac_link, "CREDENTIALS_PATH", creds_path)
    monkeypatch.setattr(mac_link, "_home_candidates", lambda: [tmp_path])
    monkeypatch.delenv("WISPR_FLOW_SUPPORT_DIR", raising=False)
    monkeypatch.delenv("MINDS_MAC_DEVICE", raising=False)

    session_bytes = _sample_session_json().encode("utf-8")
    config_bytes = json.dumps({"prefs": {"user": {"uuid": "u-2"}, "sessionId": "s-2"}}).encode("utf-8")

    def fake_latchkey(args, binary=False, timeout=60):
        joined = " ".join(args)
        if "/devices" in joined:
            return json.dumps({"devices": [{"device_id": "host-111"}]})
        if "/permissions/self" in joined:
            return json.dumps({
                "rules": [
                    {"latchkey-self": [
                        "minds-file-server-read-host-111:/Users/alice/Library/Application Support/Wispr Flow"
                    ]}
                ]
            })
        if "session.json" in joined:
            return session_bytes if binary else session_bytes.decode()
        if "config.json" in joined:
            return config_bytes if binary else config_bytes.decode()
        raise AssertionError(f"Unexpected latchkey call: {args}")

    monkeypatch.setattr(mac_link, "_latchkey", fake_latchkey)
    payload = mac_link.refresh()
    assert payload["access_token"] == "tok-example-12345"
    assert payload["user_uuid"] == "u-2"
    assert payload["source"] == "file_sharing"


def test_refresh_raises_clear_error_when_no_access_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(mac_link, "_home_candidates", lambda: [tmp_path])
    monkeypatch.setattr(mac_link, "WORKBENCH_CONFIG", tmp_path / "none")
    monkeypatch.setattr(mac_link, "DESKRUN", tmp_path / "missing-deskrun")
    monkeypatch.delenv("WISPR_FLOW_SUPPORT_DIR", raising=False)
    monkeypatch.delenv("MINDS_MAC_HOME", raising=False)
    monkeypatch.setattr(mac_link, "find_file_sharing_grant", lambda: None)

    with pytest.raises(mac_link.MacLinkError) as caught:
        mac_link.refresh()
    assert "Wispr Flow" in str(caught.value)


# ---- The routes ------------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    runner.app.config["TESTING"] = True
    return runner.app.test_client()


def test_the_service_reports_itself_up(client) -> None:
    assert client.get("/health").get_json() == {"status": "ok"}


def test_the_page_renders_with_the_record_button_and_the_shell_script(client) -> None:
    body = client.get("/").get_data(as_text=True)
    assert "Press to record" in body
    # The placeholder must be replaced, or the window loses its link to the workspace.
    assert "<!-- shell script -->" not in body
    assert "connectToShell" in body


def test_asking_to_transcribe_nothing_is_refused_in_plain_words(client) -> None:
    response = client.post("/api/transcribe")
    assert response.status_code == 400
    assert "No audio file" in response.get_json()["detail"]


def test_speaking_an_empty_line_is_refused(client) -> None:
    response = client.post("/api/speak", json={"text": "   "})
    assert response.status_code == 400
    assert "sentence" in response.get_json()["detail"]


def test_an_unknown_run_is_not_found(client) -> None:
    assert client.get("/api/runs/not-a-real-id/audio").status_code == 404
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000/raw").status_code == 404


def test_a_run_keeps_its_audio_and_wisprs_whole_reply(client, tmp_path) -> None:
    """The raw record is the point: a later change in processing must never need a refetch."""
    wav = audio.to_wav(audio.to_pcm(make_wav(seconds=0.5, rate=16000, channels=1)))
    responses = [{"state": {"rawText": {"content": "um hello"}}}, {"result": {"output": {}}}]
    summary = runner.save_run(
        {"id": "11111111-2222-3333-4444-555555555555", "at": 1, "formatted": "Hello"}, wav, responses
    )

    stored = client.get(f"/api/runs/{summary['id']}/audio")
    assert stored.status_code == 200
    assert stored.get_data() == wav
    assert json.loads(client.get(f"/api/runs/{summary['id']}/raw").get_data()) == responses
    assert client.get("/api/runs").get_json()["runs"][0]["formatted"] == "Hello"


def test_transcribing_without_a_sign_in_says_so_instead_of_failing_oddly(client, monkeypatch) -> None:
    def no_token(auto_refresh: bool = True) -> str:
        raise mac_link.CredentialsMissing("No Wispr Flow sign-in is stored here.")

    monkeypatch.setattr(mac_link, "access_token", no_token)
    wav = make_wav(seconds=1.0, rate=16000, channels=1)
    response = client.post("/api/transcribe", data={"audio": (io.BytesIO(wav), "clip.wav")})
    assert response.status_code == 409
    assert "sign-in" in response.get_json()["detail"]


def test_a_clip_too_short_to_hear_is_refused_before_wispr_is_called(client, monkeypatch) -> None:
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("Wispr was called for a clip that should have been refused")

    monkeypatch.setattr(flow_client, "transcribe", must_not_be_called)
    wav = make_wav(seconds=0.05, rate=16000, channels=1)
    response = client.post("/api/transcribe", data={"audio": (io.BytesIO(wav), "blip.wav")})
    assert response.status_code == 400
    assert "too short" in response.get_json()["detail"]


def test_a_whole_transcription_is_summarised_and_saved(client, monkeypatch) -> None:
    monkeypatch.setattr(mac_link, "access_token", lambda auto_refresh=True: "fake-token")
    monkeypatch.setattr(mac_link, "load", lambda: {"email": "someone@example.com"})
    monkeypatch.setattr(flow_client, "transcribe", lambda *a, **k: flow_client.Transcript(
        formatted="I think we should move the meeting to 3.",
        raw="Um I think we should ah move the meeting to three.",
        status="RESULT_STATUS_FORMATTED", origin="canto_1.0.1", server="bt-deployment-test",
        audio_seconds=2.0, round_trip_seconds=0.5, messages=9, responses=[{"result": {}}],
    ))

    wav = make_wav(seconds=2.0, rate=16000, channels=1)
    body = client.post("/api/transcribe", data={"audio": (io.BytesIO(wav), "clip.wav")}).get_json()

    assert body["formatted"].startswith("I think we should move")
    assert body["raw"].startswith("Um I think")
    assert body["origin"] == "canto_1.0.1"
    assert body["diff"]["edits"] >= 2
    assert body["account"] == "someone@example.com"
    # Saved, so the page can redraw it later without asking Wispr again.
    assert client.get(f"/api/runs/{body['id']}/raw").status_code == 200


def test_wispr_refusing_the_sign_in_is_reported_as_a_bad_gateway(client, monkeypatch) -> None:
    monkeypatch.setattr(mac_link, "access_token", lambda auto_refresh=True: "fake-token")
    monkeypatch.setattr(mac_link, "load", lambda: {})

    def refuse(*args, **kwargs):
        raise flow_client.FlowError("Wispr Flow turned down the sign-in.")

    monkeypatch.setattr(flow_client, "transcribe", refuse)
    wav = make_wav(seconds=1.0, rate=16000, channels=1)
    response = client.post("/api/transcribe", data={"audio": (io.BytesIO(wav), "clip.wav")})
    assert response.status_code == 502
    assert "turned down" in response.get_json()["detail"]


def test_every_page_carries_its_content_rules(client) -> None:
    policy = client.get("/").headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]


def test_v1_audio_transcriptions_openai_drop_in(client, monkeypatch) -> None:
    monkeypatch.setattr(mac_link, "access_token", lambda auto_refresh=True: "fake-token")
    monkeypatch.setattr(mac_link, "load", lambda: {"email": "adopter@example.com"})
    monkeypatch.setattr(flow_client, "transcribe", lambda *a, **k: flow_client.Transcript(
        formatted="Hello from my phone essay tool.",
        raw="um hello from my phone essay tool",
        status="RESULT_STATUS_FORMATTED", origin="canto_1.0.1", server="bt-deployment-test",
        audio_seconds=1.5, round_trip_seconds=0.4, messages=5, responses=[{"result": {}}],
    ))

    wav = make_wav(seconds=1.5, rate=16000, channels=1)

    res_json = client.post("/v1/audio/transcriptions", data={"file": (io.BytesIO(wav), "dictation.m4a")})
    assert res_json.status_code == 200
    assert res_json.get_json() == {"text": "Hello from my phone essay tool."}

    res_text = client.post(
        "/v1/audio/transcriptions",
        data={"file": (io.BytesIO(wav), "dictation.m4a"), "response_format": "text"},
    )
    assert res_text.status_code == 200
    assert res_text.get_data(as_text=True) == "Hello from my phone essay tool."

    res_verbose = client.post(
        "/v1/audio/transcriptions",
        data={"file": (io.BytesIO(wav), "dictation.m4a"), "response_format": "verbose_json"},
    )
    assert res_verbose.status_code == 200
    body = res_verbose.get_json()
    assert body["text"] == "Hello from my phone essay tool."
    assert body["raw"] == "um hello from my phone essay tool"
