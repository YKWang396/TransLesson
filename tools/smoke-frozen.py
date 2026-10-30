"""Check the portable package without developer Python/Node/FFmpeg on PATH."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import uuid
import argparse
import copy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from mathbridge.storage.project_store import ProjectStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--https', action='store_true', help='Also check real HTTPS with an intentionally invalid API key (expects HTTP 401/403).')
    args = parser.parse_args()
    bundle = ROOT / 'dist/TransLesson'
    ssl_policy = Path(sys.base_prefix) / 'Lib' / 'codex-rejected-cas.pem'
    if ssl_policy.is_file():
        assert (bundle/'_internal'/ssl_policy.name).read_bytes() == ssl_policy.read_bytes(), 'Missing Python TLS policy'
    for name in ('libssl-3-x64.dll','libcrypto-3-x64.dll'):
        assert hashlib.sha256((bundle/'_internal'/name).read_bytes()).digest() == hashlib.sha256(
            (Path(sys.base_prefix)/'DLLs'/name).read_bytes()).digest(), 'Mismatched Python TLS dependency: '+name
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
    if args.https:
        probe = copy.deepcopy(project)
        probe['segments'] = probe['segments'][:1]
        probe['notes'] = []; probe['formulas'] = []; probe['glossary'] = []
        probe['segments'][0].update(source_text='Two plus three equals five.', target_text='',
                                    subtitle_plain='', tts_text='', formula_ids=[], term_ids=[])
        probe_dir = ROOT/'tmp'/('frozen-https-'+uuid.uuid4().hex[:10])
        ProjectStore(probe_dir).save(probe)
        command = dict(type='command', stage='translate', request_id='req-https-'+uuid.uuid4().hex,
                       project_dir=str(probe_dir), project=probe, project_revision=probe['revision'],
                       source_path='', options={}, settings=dict(text_provider='deepseek', model='deepseek-flash',
                       api_base='https://api.deepseek.com', api_key='invalid-key-for-tls-smoke', allow_text_api=True,
                       budget_cny=1, max_calls=3, input_price_per_million=2, output_price_per_million=8))
        result = subprocess.run([str(bundle/'translesson-worker.exe'), '--worker'],
                                input=json.dumps(command)+'\n', text=True, encoding='utf-8',
                                capture_output=True, env=environment, cwd=bundle, timeout=30)
        done = next((json.loads(line) for line in result.stdout.splitlines()
                     if line.strip() and json.loads(line).get('type')=='done'), {})
        assert 'API_AUTH_FAILED' in done.get('message',''), (done, result.stderr)
        print('PASS: frozen HTTPS reached API authentication; no valid key used')
    screenshot = ROOT / 'artifacts/ui-portable.png'
    subprocess.run([str(bundle / '课译TransLesson.exe'), str(project_dir), '--smoke-test', '--screenshot', str(screenshot)],
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
        result = subprocess.run([str(bundle / 'translesson-worker.exe'), '--worker'], input=json.dumps(command, ensure_ascii=False) + '\n',
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
