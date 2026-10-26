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

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def main():
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
    package_root = ROOT / 'dist/MathBridge'
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
    report = {'platform': platform.platform(), 'python': sys.version, 'packages': records,
              'node': {'version': node_version, 'sha256': sha(node) if node else None, 'license_status': 'Upstream Node.js and bundled third-party license collection required before public redistribution'},
              'mathjax': {'version': mathjax_data['version'], 'license': mathjax_data['license']},
              'ffmpeg': {'version_and_configuration': ffmpeg_version, 'sha256': sha(ffmpeg), 'license': 'GPL version 3 or later per actual -L output; compatible corresponding source distribution must be arranged before public redistribution'},
              'fonts': 'Windows fonts read from local OS and embedded in generated PDFs; font binaries are not redistributed',
              'model_weights': 'No downloaded ASR, OCR, language or voice-cloning weights included',
              'release_status': 'Local development/portable test build; clean-machine and public distribution license audit not complete'}
    (ROOT / 'packaging/windows/runtime-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copyfile(ROOT / 'packaging/windows/runtime-manifest.json', package_root / 'runtime-manifest.json')
    shutil.copytree(ROOT / 'docs', package_root / 'docs', dirs_exist_ok=True)
    (package_root / '使用说明.md').write_text('# 课译 MathBridge 0.1\n\n双击 MathBridge.exe 启动。请保留整个文件夹。\n\n[详细使用说明](docs/08-user-guide.md) · [多人原声配音配置](docs/11-voice-backends.md) · [实现与验证范围](docs/09-implementation-status.md)\n\nWhisper 和说话人识别模型需要另行准备；文本 API 需要 Key 和文本上传许可。每人可配置独立系统声音，或通过本机 GPT-SoVITS 服务参考原声配音。克隆模型及服务未打包，真实音色相似度和长课效果需另行验收。\n', encoding='utf-8')
    print(f'Recorded {len(records)} installed runtime packages and available license files')


if __name__ == '__main__':
    main()
