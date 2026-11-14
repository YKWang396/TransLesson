"""Record installed runtime versions, binary fingerprints and available licenses."""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def main():
    application_version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    wanted = ['PySide6-Essentials', 'PySide6-Addons', 'jsonschema', 'imageio-ffmpeg', 'reportlab', 'Pillow', 'faster-whisper', 'sherpa-onnx']
    resolved = {}
    while wanted:
        name = wanted.pop()
        dist = metadata.distribution(name)
        key = dist.metadata['Name']
        if key.lower().replace('_', '-') in resolved:
            continue
        record = {'name': key, 'version': dist.version,
                  'license': dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'See included license files',
                  'project_urls': dist.metadata.get_all('Project-URL', [])}
        resolved[key.lower().replace('_', '-')] = record
        for dependency in dist.requires or []:
            requirement = Requirement(dependency)
            if requirement.marker is None or requirement.marker.evaluate({'extra': ''}):
                wanted.append(requirement.name)
    records = sorted(resolved.values(), key=lambda r: r['name'].lower())
    package_root = ROOT / 'dist/TransLesson'
    notices = package_root / 'licenses'
    notices.mkdir(parents=True, exist_ok=True)
    for record in records:
        dist = metadata.distribution(record['name'])
        copied = []
        for relative in dist.files or []:
            if any(s in relative.name.lower() for s in ('license', 'copying', 'copyright', 'notice')) and not str(relative).endswith('.py'):
                source = Path(dist.locate_file(relative))
                if source.is_file() and source.stat().st_size < 2_000_000:
                    directory = notices / record['name']
                    directory.mkdir(exist_ok=True)
                    target = directory / relative.name
                    shutil.copyfile(source, target)
                    copied.append(target.relative_to(package_root).as_posix())
        record['license_files'] = copied
    lock = '# Tested Windows 11 x64 / Python 3.12.14; CPU ASR + speaker diarization; no bundled model weights.\n'
    lock += '\n'.join(f"{r['name']}=={r['version']}" for r in records) + '\n'
    (ROOT / 'requirements-windows.lock.txt').write_text(lock, encoding='utf-8')
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg_version = subprocess.check_output([ffmpeg, '-version'], text=True)
    ffmpeg_license = subprocess.run([ffmpeg, '-L'], capture_output=True, text=True)
    (notices / 'FFmpeg-build-license.txt').write_text(ffmpeg_license.stdout + ffmpeg_license.stderr, encoding='utf-8')
    node = shutil.which('node')
    node_version = subprocess.check_output([node, '--version'], text=True).strip() if node else None
    mathjax = ROOT / 'src/mathbridge/handout/node_modules/mathjax-full/package.json'
    mathjax_data = json.loads(mathjax.read_text(encoding='utf-8'))
    shutil.copyfile(mathjax.with_name('LICENSE'), notices / 'MathJax-LICENSE.txt')
    def sha(path):
        digest = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    report = {'application_name': '课译TransLesson', 'application_version': application_version,
              'documentation_files': sorted(p.relative_to(ROOT).as_posix() for p in (ROOT/'docs').glob('*.md')),
              'platform': platform.platform(), 'python': sys.version, 'packages': records,
              'node': {'version': node_version, 'sha256': sha(node) if node else None, 'license_status': 'Upstream Node.js and bundled third-party license collection required before public redistribution'},
              'mathjax': {'version': mathjax_data['version'], 'license': mathjax_data['license']},
              'ffmpeg': {'version_and_configuration': ffmpeg_version, 'sha256': sha(ffmpeg), 'license': 'GPL version 3 or later per actual -L output; compatible corresponding source distribution must be arranged before public redistribution'},
              'fonts': 'Windows fonts read from local OS and embedded in generated PDFs; font binaries are not redistributed',
              'model_weights': 'No downloaded ASR, OCR, language or voice-cloning weights included',
              'release_status': 'Local development/portable test build; clean-machine and public distribution license audit not complete'}
    (ROOT / 'packaging/windows/runtime-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copyfile(ROOT / 'packaging/windows/runtime-manifest.json', package_root / 'runtime-manifest.json')
    shutil.copytree(ROOT / 'docs', package_root / 'docs', dirs_exist_ok=True)
    components=package_root/'components'
    components.mkdir(exist_ok=True)
    shutil.copyfile(ROOT/'components/README.md',components/'README.md')
    resources=ROOT/'src/mathbridge/resources'
    for original,name in [('gpt-sovits-service.ps1','gpt-sovits-service.ps1'),
                          ('start-voice-service.cmd','启动声音服务.cmd'),('stop-voice-service.cmd','停止声音服务.cmd')]:
        shutil.copyfile(resources/original,components/name)
    (package_root / '使用说明.md').write_text(
        '# 课译TransLesson '+application_version+'\n\n双击课译TransLesson.exe 启动，请保留整个文件夹。\n\n'
        '本地组件放在 components/GPT-SoVITS 和 components/faster-whisper-small，应用会自动识别；也可在处理设置中指定组件目录。'
        '在 03 配音点击“启动声音服务”，等待显示已就绪后使用原声音色。\n\n'
        '[操作指南](docs/08-user-guide.md) · [文本 API](docs/20-multiple-text-apis.md) · [声音配置](docs/11-voice-backends.md) · [安装与迁移](docs/12-another-computer.md)\n\n'
        '项目与课程视频单独保存；文本 API 使用所配置的 Key。常规程序下载包不包含大型模型和 GPT-SoVITS 运行环境，迁移完整本机软件时请一并复制 components。\n',encoding='utf-8')
    print(f'Recorded {len(records)} installed runtime packages and available license files')


if __name__ == '__main__':
    main()
