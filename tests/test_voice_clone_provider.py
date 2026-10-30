from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import struct
import threading
import time
import wave

import pytest

from mathbridge.media.engine import CancelledError, MediaError
from mathbridge.providers.tts import gpt_sovits


def wav_bytes(duration=3.5):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"".join(struct.pack("<h", round(10000 * math.sin(2 * math.pi * 220 * i / 16000)))
                                   for i in range(round(duration * 16000))))
    return output.getvalue()


@pytest.fixture
def reference(tmp_path):
    path = tmp_path / "reference.wav"
    path.write_bytes(wav_bytes())
    return path


@contextmanager
def local_api(*, body=None, content_type="audio/wav", status=200, delay=0):
    requests, entered = [], threading.Event()
    if body is None:
        body = wav_bytes(.2)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            requests.append((self.path, json.loads(self.rfile.read(size))))
            entered.set()
            if delay:
                time.sleep(delay)
            self.reply()

        def do_GET(self):
            requests.append((self.path, None))
            self.reply()

        def reply(self):
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                if status == 302:
                    self.send_header("Location", "https://example.com/tts")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, entered
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def call(reference, output, endpoint, **kwargs):
    return gpt_sovits.synthesize("这是译文。", "zh-CN", reference,
                                  "The derivative is positive.", "en-US", output,
                                  endpoint=endpoint, **kwargs)


def test_real_http_wav_and_cross_language_payload(reference, tmp_path):
    output = tmp_path / "nested" / "dub.wav"
    with local_api() as (endpoint, requests, _):
        result = call(reference, output, endpoint + "/tts", speed=1.15)
    assert result == output
    assert gpt_sovits._wav_info(output)["duration_seconds"] == .2
    assert len(requests) == 1
    path, payload = requests[0]
    assert path == "/tts"
    assert payload["text"] == "这是译文。"
    assert payload["text_lang"] == "zh"
    assert payload["prompt_lang"] == "en"
    assert payload["prompt_text"] == "The derivative is positive."
    assert payload["ref_audio_path"] == str(reference.resolve())
    assert payload["streaming_mode"] is False
    assert payload["media_type"] == "wav"
    assert payload["speed_factor"] == 1.15


def test_multiple_references_are_distinct_wavs_not_appended_to_prompt(reference,tmp_path):
    auxiliary=tmp_path/'aux.wav';auxiliary.write_bytes(wav_bytes(5))
    with local_api() as (endpoint,requests,_):
        call(reference,tmp_path/'out.wav',endpoint,auxiliary_reference_audio=[auxiliary])
    payload=requests[0][1]
    assert payload['aux_ref_audio_paths']==[str(auxiliary.resolve())]
    assert payload['prompt_text']=='The derivative is positive.'
    with pytest.raises(MediaError,match='INVALID_REFERENCE'):
        call(reference,tmp_path/'out.wav',endpoint,auxiliary_reference_audio=[reference])


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1:9880", "http://192.168.1.2:9880",
    "http://example.com", "http://127.0.0.1.evil.test:9880", "http://127.0.0.1:0",
    "http://user:pass@127.0.0.1:9880", "http://127.0.0.1:9880/tts?token=x",
    "http://127.0.0.1:9880/other", "http://[::1%25zone]:9880"])
def test_remote_or_ambiguous_endpoint_never_connects(reference, tmp_path, monkeypatch, endpoint):
    def forbidden(*_, **__):
        pytest.fail("invalid endpoint attempted HTTP")
    monkeypatch.setattr(gpt_sovits.http.client, "HTTPConnection", forbidden)
    with pytest.raises(MediaError, match="INVALID_VOICE"):
        call(reference, tmp_path / "dub.wav", endpoint)


def test_loopback_endpoint_normalization():
    assert gpt_sovits.normalize_endpoint("http://localhost:9880/tts/") == "http://127.0.0.1:9880"
    assert gpt_sovits.normalize_endpoint("http://[::1]:9880") == "http://[::1]:9880"


def test_health_probe_checks_real_api_contract():
    contract = {"paths": {"/tts": {"post": {"requestBody": {"required": True}}}}}
    with local_api(body=json.dumps(contract).encode(), content_type="application/json") as (endpoint, requests, _):
        result = gpt_sovits.probe(endpoint)
    assert requests == [("/openapi.json", None)]
    assert result["reachable"] is True
    assert result["synthesis_verified"] is False
    with local_api(body=b"{}", content_type="application/json") as (endpoint, _, _):
        with pytest.raises(MediaError, match="TTS_UNAVAILABLE"):
            gpt_sovits.probe(endpoint)


@pytest.mark.parametrize("duration", [2.9, 10.1])
def test_reference_requires_clean_three_to_ten_seconds(reference, tmp_path, duration):
    reference.write_bytes(wav_bytes(duration))
    with pytest.raises(MediaError, match="INVALID_REFERENCE"):
        call(reference, tmp_path / "dub.wav", "http://127.0.0.1:1")


def test_invalid_response_preserves_previous_output(reference, tmp_path):
    output = tmp_path / "dub.wav"
    output.write_bytes(b"previous successful audio")
    truncated = wav_bytes(.2)[:-20]
    with local_api(body=truncated) as (endpoint, _, _):
        with pytest.raises(MediaError, match="TTS_FAILED"):
            call(reference, output, endpoint)
    assert output.read_bytes() == b"previous successful audio"
    assert not list(tmp_path.glob(".*.tmp.wav"))


@pytest.mark.parametrize("body,content_type,status,code", [
    (b'{"message":"missing model"}', "application/json", 400, "HTTP 400"),
    (b'{"message":"not audio"}', "application/json", 200, "TTS_FAILED"),
    (b"redirect", "text/plain", 302, "HTTP 302"),
])
def test_errors_and_redirects_are_not_audio(reference, tmp_path, body, content_type, status, code):
    output = tmp_path / "dub.wav"
    with local_api(body=body, content_type=content_type, status=status) as (endpoint, requests, _):
        with pytest.raises(MediaError, match=code):
            call(reference, output, endpoint)
    assert len(requests) == 1
    assert not output.exists()


def test_cancellation_while_waiting_for_headers_is_prompt_and_has_no_late_output(reference, tmp_path):
    output, cancel = tmp_path / "dub.wav", threading.Event()
    with local_api(delay=1.5) as (endpoint, _, entered):
        def request_cancel():
            entered.wait(2)
            cancel.set()
        thread = threading.Thread(target=request_cancel, daemon=True)
        thread.start()
        started = time.monotonic()
        with pytest.raises(CancelledError):
            call(reference, output, endpoint, cancel=cancel, timeout=5)
        assert time.monotonic() - started < .8
        thread.join(2)
    assert not output.exists()


def test_timeout_has_no_output(reference, tmp_path):
    output = tmp_path / "dub.wav"
    with local_api(delay=1) as (endpoint, _, _):
        with pytest.raises(MediaError, match="PROVIDER_TIMEOUT"):
            call(reference, output, endpoint, timeout=.1)
    assert not output.exists()


def test_cancelled_before_request_does_not_connect(reference, tmp_path, monkeypatch):
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(gpt_sovits.http.client, "HTTPConnection", lambda *_args, **_kwargs: pytest.fail("connected"))
    with pytest.raises(CancelledError):
        call(reference, tmp_path / "dub.wav", "http://127.0.0.1:9880", cancel=cancel)
