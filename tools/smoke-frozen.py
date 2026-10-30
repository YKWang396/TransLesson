"""Check the portable package without developer Python/Node/FFmpeg on PATH."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from mathbridge.storage.project_store import ProjectStore


def main():
    bundle = ROOT / 'dist/MathBridge'
    report = json.loads((ROOT / 'artifacts/pipeline-report.json').read_text(encoding='utf-8'))
    project_dir = Path(report['results'][0]['outputs'][0]).parents[2]
    store = ProjectStore(project_dir)
    project = store.load()
    environment = os.environ.copy()
    system32 = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32'
    environment['PATH'] = str(system32) + os.pathsep + str(system32 / 'WindowsPowerShell/v1.0')
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['PYTHONIOENCODING'] = 'utf-8'
    environment['PYTHONUTF8'] = '1'
    environment.pop('PYTHONPATH', None)
    screenshot = ROOT / 'artifacts/ui-portable.png'
    subprocess.run([str(bundle / 'MathBridge.exe'), str(project_dir), '--smoke-test', '--screenshot', str(screenshot)],
                   env=environment, cwd=bundle, timeout=30, check=True)
    assert screenshot.is_file() and screenshot.stat().st_size > 5000
    results = []
    artifacts = []
    for stage, options in [('frame', {'time_ms': 2000}), ('dub', {}),
                           ('export', {'formats': ['srt', 'vtt', 'pdf', 'video', 'wav'], 'background': 'dub_only'})]:
        command = {'type': 'command', 'request_id': 'req-frozen-' + uuid.uuid4().hex,
                   'stage': stage, 'project_dir': str(project_dir), 'project': project,
                   'project_revision': project['revision'], 'source_path': store.source_path,
                   'settings': {}, 'options': options, 'artifacts': artifacts}
        result = subprocess.run([str(bundle / 'mathbridge-worker.exe'), '--worker'], input=json.dumps(command, ensure_ascii=False) + '\n',
                                text=True, encoding='utf-8', capture_output=True, env=environment, cwd=bundle, timeout=60)
        messages = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        done = next((m for m in messages if m.get('type') == 'done'), {})
        assert done.get('status') == 'succeeded', (done, result.stderr)
        artifacts.extend(done['result'].get('artifacts', []))
        results.append({'stage': stage, 'outputs': done['result']['outputs']})
    (ROOT / 'artifacts/frozen-report.json').write_text(json.dumps({'path_isolated': True, 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: portable GUI, isolated PATH, frozen worker pipes, bundled FFmpeg, Node/MathJax, QtSvg, PDF, real Windows TTS and MP4 export')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
