"""Verify portable updates retire managed docs while retaining user components."""
from pathlib import Path
import hashlib
import json
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[1]


def main():
    target=ROOT/'tmp'/('deployment-'+uuid.uuid4().hex[:10]);target.mkdir()
    for relative in ['docs/10-validation-report.md','docs/replaced.md','docs/my-course-notes.md',
                     'components/GPT-SoVITS/user-model.txt']:
        path=target/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('retained fixture',encoding='utf8')
    (target/'runtime-manifest.json').write_text(json.dumps(dict(documentation_files=['docs/replaced.md'])),encoding='utf8')
    subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/deploy-local.ps1'),
                    '-InstallRoot',str(target),'-SkipShortcut'],check=True)
    assert not (target/'docs/10-validation-report.md').exists()
    assert not (target/'docs/replaced.md').exists()
    assert (target/'docs/my-course-notes.md').read_text('utf8')=='retained fixture'
    assert (target/'components/GPT-SoVITS/user-model.txt').read_text('utf8')=='retained fixture'
    for name in ['课译TransLesson.exe','translesson-worker.exe']:
        assert hashlib.sha256((target/name).read_bytes()).digest()==hashlib.sha256((ROOT/'dist/TransLesson'/name).read_bytes()).digest()
    print('PASS deployment: current binaries, retired docs removed, custom docs/components retained, desktop shortcut untouched')


if __name__=='__main__':main()
