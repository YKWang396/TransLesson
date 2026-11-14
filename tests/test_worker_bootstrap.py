"""Keep stdin open while checking native ASR imports in a real subprocess."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import pytest


@pytest.mark.parametrize('stage',['transcribe','dub','preview','export'])
def test_asr_native_import_does_not_deadlock_with_cancel_pipe_open(tmp_path,stage):
    if importlib.util.find_spec("faster_whisper") is None:
        pytest.skip("Optional ASR runtime is not installed")
    source = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment.update(PYTHONPATH=str(source), PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    # Only replace the expensive model stage. The worker's stdin cancellation
    # thread and the real NumPy/faster-whisper native imports stay intact.
    script = """
import mathbridge.worker.__main__ as worker
def execute(command, cancel, report):
    import numpy
    import faster_whisper
    return {'outputs': [], 'metadata': {'native_imports_ready': True}}
worker.execute = execute
raise SystemExit(worker.main())
"""
    records = queue.Queue()
    with (tmp_path / "worker.stderr.log").open("w", encoding="utf-8") as errors:
        process = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", env=environment)
        def read():
            for line in process.stdout:
                records.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            (tmp_path/'model.bin').write_bytes(b'only native imports are exercised')
            process.stdin.write(json.dumps(dict(stage=stage, request_id="req-native-import", project_revision=1,
                project=dict(speakers=[dict(voice_mode='reference')]),
                settings=dict(whisper_model_path=str(tmp_path)),options=dict(formats=['video'])))+"\n")
            process.stdin.flush()
            deadline = time.monotonic()+20
            done = None
            while time.monotonic() < deadline:
                try:
                    record = records.get(timeout=.1)
                except queue.Empty:
                    continue
                if record.get("type") == "done":
                    done = record
                    break
            assert done is not None, "ASR imports stalled while the cancellation pipe remained open"
            assert done["status"] == "succeeded", done
            assert done["result"]["metadata"]["native_imports_ready"]
        finally:
            try:
                if process.poll() is None:
                    process.stdin.write('{"type":"cancel"}\n')
                    process.stdin.flush()
                process.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=2)
            process.stdout.close()


def test_standard_dubbing_does_not_require_optional_asr_runtime(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'src'
    environment = dict(os.environ,PYTHONPATH=str(source),PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    (tmp_path/'model.bin').write_bytes(b'configured model without an ASR runtime')
    script = """
import builtins
import mathbridge.worker.__main__ as worker
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy', 'faster_whisper'}:
        raise ImportError('Optional recognizer must not be required for system voices')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
worker.execute = lambda *args: {'outputs': [], 'metadata': {'standard_ready': True}}
raise SystemExit(worker.main())
"""
    command = dict(stage='dub',request_id='req-standard',project=dict(speakers=[dict(voice_mode='standard')]),
                   settings=dict(whisper_model_path=str(tmp_path)))
    result = subprocess.run([sys.executable,'-u','-c',script],input=json.dumps(command)+'\n',
                            text=True,encoding='utf8',capture_output=True,env=environment,timeout=20)
    done = next(m for line in result.stdout.splitlines() if (m:=json.loads(line)).get('type')=='done')
    assert done['status']=='succeeded',done
    assert done['result']['metadata']['standard_ready']
