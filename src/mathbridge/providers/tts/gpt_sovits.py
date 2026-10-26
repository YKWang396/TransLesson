"""Reference-voice TTS through a user-started, loopback-only GPT-SoVITS API.

The API reads a local reference path on the same machine. This adapter never
starts a service, downloads weights, changes its model, follows redirects, or
falls back to a system voice. No additional client dependencies are needed.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import threading
import time
from urllib.parse import urlsplit
import uuid
import wave

from ...media.engine import CancelledError, MediaError


DEFAULT_ENDPOINT = "http://127.0.0.1:9880"
_MAX_AUDIO_BYTES = 64 * 1024 * 1024
_MAX_JSON_BYTES = 256 * 1024


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def _endpoint(endpoint):
    try:
        parts = urlsplit(str(endpoint).strip())
        host, port = parts.hostname, parts.port if parts.port is not None else 9880
        if host == "localhost":
            host = "127.0.0.1"  # Avoid DNS resolution/rebinding, and ignore proxies.
        address = ipaddress.ip_address(host or "")
        if (parts.scheme != "http" or not address.is_loopback or "%" in host
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or not 1 <= port <= 65535
                or parts.path.rstrip("/") not in ("", "/tts")):
            raise ValueError()
    except (TypeError, ValueError):
        raise MediaError("INVALID_VOICE", "参考原声服务地址必须为本机 HTTP 地址，例如 http://127.0.0.1:9880。") from None
    return str(address), port


def normalize_endpoint(endpoint=DEFAULT_ENDPOINT):
    """Return the canonical loopback root address, for settings/fingerprints."""
    host, port = _endpoint(endpoint)
    return f"http://{'[' + host + ']' if ':' in host else host}:{port}"


def _language(language):
    value = str(language).lower().replace("_", "-").split("-")[0]
    if value not in ("en", "zh"):
        raise MediaError("LANGUAGE_UNAVAILABLE", "当前参考原声配音支持中文和英语。")
    return value


def _request(endpoint, method, path, *, payload=None, timeout=180, cancel=None,
             max_bytes=_MAX_AUDIO_BYTES):
    """Read bounded bytes off-thread so cancellation also interrupts headers.

    Only the caller writes files. An abandoned inference can therefore never
    commit a late output. Closing the socket cancels the client; inference may
    continue in the separately managed GPT-SoVITS process.
    """
    _check(cancel)
    host, port = _endpoint(endpoint)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise MediaError("INVALID_VOICE", "参考原声服务超时设置必须为正数。")
    connection = http.client.HTTPConnection(host, port, timeout=float(timeout))
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    result, done, stopped = {}, threading.Event(), threading.Event()

    def run():
        try:
            connection.connect()
            result["socket"] = connection.sock
            if stopped.is_set():
                return
            headers = {"Accept": "audio/wav, application/json", "Connection": "close"}
            if request_body is not None:
                headers["Content-Type"] = "application/json; charset=utf-8"
            connection.request(method, path, body=request_body, headers=headers)
            response = connection.getresponse()
            result["status"] = response.status
            result["content_type"] = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            limit = max_bytes if response.status == 200 else min(max_bytes, 64 * 1024)
            length = response.getheader("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                    if length < 0 or length > limit:
                        raise ValueError()
                except ValueError:
                    raise MediaError("TTS_FAILED", "参考原声服务响应长度无效或超过限制。") from None
            chunks, size = [], 0
            while True:
                chunk = response.read(min(65536, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise MediaError("TTS_FAILED", "参考原声服务响应超过大小限制。")
                chunks.append(chunk)
            result["body"] = b"".join(chunks)
        except Exception as exc:
            result["error"] = exc
        finally:
            connection.close()
            done.set()

    def close():
        stopped.set()
        try:
            active_socket = result.get("socket") or connection.sock
            if active_socket is not None:
                active_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    threading.Thread(target=run, name="mathbridge-local-reference-tts", daemon=True).start()
    deadline = time.monotonic() + timeout
    try:
        while not done.wait(0.05):
            _check(cancel)
            if time.monotonic() >= deadline:
                raise MediaError("PROVIDER_TIMEOUT", "参考原声配音超时，请检查本地服务；服务端可能仍在计算。")
        _check(cancel)
        error = result.get("error")
        if error is not None:
            if isinstance(error, MediaError):
                raise error
            if isinstance(error, (TimeoutError, socket.timeout)):
                raise MediaError("PROVIDER_TIMEOUT", "参考原声配音超时，请检查本地服务。") from error
            raise MediaError("TTS_UNAVAILABLE", "无法读取 GPT-SoVITS 本地服务，请检查地址、服务状态及模型是否加载。") from error
        if result["status"] != 200:
            message = ""
            try:
                value = json.loads(result["body"])
                if isinstance(value, dict):
                    message = str(value.get("message") or value.get("detail") or "")[:500]
            except (ValueError, UnicodeDecodeError):
                pass
            raise MediaError("TTS_FAILED", f"参考原声服务返回 HTTP {result['status']}。{message}")
        return result["content_type"], result["body"]
    finally:
        close()


def _wav_info(path, *, reference=False):
    try:
        with wave.open(str(path), "rb") as audio:
            channels, rate, width, frames = (audio.getnchannels(), audio.getframerate(),
                                             audio.getsampwidth(), audio.getnframes())
            if (audio.getcomptype() != "NONE" or channels not in (1, 2) or width not in (1, 2, 3, 4)
                    or not 8000 <= rate <= 192000 or frames <= 0):
                raise ValueError()
            duration = frames / rate
            if reference and not 3 <= duration <= 10:
                raise MediaError("INVALID_REFERENCE", "参考原声须为 3–10 秒 WAV，建议选择单人连续说话的 4–8 秒片段。")
            if not reference and not 0.05 <= duration <= 600:
                raise ValueError()
            # A WAV header alone is insufficient: verify every declared sample.
            remaining = frames * channels * width
            while remaining:
                data = audio.readframes(min(frames, 65536))
                if not data:
                    raise ValueError()
                remaining -= len(data)
            if remaining != 0:
                raise ValueError()
    except MediaError:
        raise
    except (OSError, EOFError, ValueError, wave.Error):
        code = "INVALID_REFERENCE" if reference else "TTS_FAILED"
        label = "参考音频" if reference else "参考原声服务返回的音频"
        raise MediaError(code, f"{label}不是完整有效的 PCM WAV。") from None
    return {"duration_seconds": duration, "sample_rate": rate, "channels": channels}


def capabilities():
    """Describe the adapter; this does not claim a running/installed backend."""
    return {"provider": "gpt-sovits", "languages": ["en", "zh"],
            "cross_language_reference": True, "reference_duration_seconds": [3, 10],
            "audio_formats": ["wav"], "requires_local_service": True,
            "model_bundled": False, "cancellation": "client_socket"}


def probe(endpoint=DEFAULT_ENDPOINT, *, timeout=5, cancel=None):
    """Check the FastAPI contract without loading a model or generating audio.

    GPT-SoVITS loads its configured pipeline before serving this route. Actual
    voice quality and successful synthesis still require a reference trial.
    """
    content_type, data = _request(endpoint, "GET", "/openapi.json", timeout=timeout,
                                  cancel=cancel, max_bytes=_MAX_JSON_BYTES)
    try:
        value = json.loads(data)
        operation = value["paths"]["/tts"]["post"]
        if not isinstance(operation, dict) or not operation.get("requestBody"):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        raise MediaError("TTS_UNAVAILABLE", "本地服务未提供 GPT-SoVITS api_v2 的 POST /tts 接口。") from None
    return {"provider": "gpt-sovits", "reachable": True,
            "endpoint": normalize_endpoint(endpoint), "synthesis_verified": False}


def synthesize(text, language, reference_audio, reference_text, reference_language,
               output_path, *, endpoint=DEFAULT_ENDPOINT, speed=1.0, timeout=180,
               cancel=None) -> Path:
    """Generate a target-language WAV using one speaker's original reference.

    The caller must extract a 3–10 second single-speaker WAV into its project
    and supply the exact source transcript. Every speaker uses their own call.
    """
    _check(cancel)
    _endpoint(endpoint)
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise MediaError("INVALID_TRANSLATION", "参考原声配音读法须为 1–20,000 字符。")
    if not isinstance(reference_text, str) or not reference_text.strip() or len(reference_text) > 5000:
        raise MediaError("INVALID_REFERENCE", "请填写参考音频实际说出的原文（最多 5,000 字符）。")
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise MediaError("INVALID_VOICE", "参考原声语速应在 0.5–2.0 之间。")
    language, reference_language = _language(language), _language(reference_language)
    try:
        reference = Path(reference_audio).resolve(strict=True)
        if not reference.is_file():
            raise OSError()
    except (TypeError, ValueError, OSError):
        raise MediaError("INVALID_REFERENCE", "找不到参考原声 WAV，请重新选取原视频片段。") from None
    _wav_info(reference, reference=True)
    output = Path(output_path).resolve()
    if output == reference:
        raise MediaError("INVALID_REFERENCE", "配音输出不能覆盖参考原声。")
    payload = {"text": text.strip(), "text_lang": language,
               "ref_audio_path": str(reference), "prompt_text": reference_text.strip(),
               "prompt_lang": reference_language, "media_type": "wav",
               "streaming_mode": False, "speed_factor": float(speed),
               "batch_size": 1, "text_split_method": "cut5", "seed": 0}
    content_type, data = _request(endpoint, "POST", "/tts", payload=payload,
                                  timeout=timeout, cancel=cancel)
    if content_type not in ("audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"):
        raise MediaError("TTS_FAILED", "参考原声服务未返回 WAV 音频，请检查服务错误日志。")
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        raise MediaError("TTS_FAILED", "参考原声服务返回了无效 WAV。")
    _check(cancel)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.tmp.wav")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _wav_info(temporary)
        _check(cancel)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
