# Build from project root with: .venv/Scripts/python.exe -m PyInstaller packaging/windows/TransLesson.spec
import os
import shutil
import sys
import tomllib
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct

root = Path(SPECPATH).resolve().parents[1]
app_version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf8'))['project']['version']
version_tuple = tuple(int(n) for n in app_version.split('.')) + (0,)
def file_version(filename):
    return VSVersionInfo(ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple,
        mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[
        StringFileInfo([StringTable('080404b0',[
            StringStruct('FileDescription','课译TransLesson · 课程翻译工作台'),
            StringStruct('ProductName','课译TransLesson'),
            StringStruct('InternalName','TransLesson'),
            StringStruct('FileVersion',app_version),
            StringStruct('ProductVersion',app_version),
            StringStruct('OriginalFilename',filename)])]),
        VarFileInfo([VarStruct('Translation',[2052,1200])])])
node = os.environ.get('MATHBRIDGE_NODE') or shutil.which('node')
if not node:
    raise RuntimeError('Node.js is required to bundle offline MathJax')
handout = root / 'src' / 'mathbridge' / 'handout'
if not (handout / 'node_modules' / 'mathjax-full').exists():
    raise RuntimeError('Run handout/install-mathjax.ps1 before packaging')
datas = [
    (str(root / 'src/mathbridge/resources'), 'mathbridge/resources'),
    (str(root / 'src/mathbridge/schemas'), 'mathbridge/schemas'),
    (str(handout / 'render_math.cjs'), 'mathbridge/handout'),
    (str((handout / 'node_modules/mathjax-full/js').resolve()), 'mathbridge/handout/node_modules/mathjax-full/js'),
    (str((handout / 'node_modules/mathjax-full/package.json').resolve()), 'mathbridge/handout/node_modules/mathjax-full'),
    (str((handout / 'node_modules/mathjax-full/LICENSE').resolve()), 'mathbridge/handout/node_modules/mathjax-full'),
]
# Some Python distributions load an adjacent CA policy file whenever an SSL
# context is created. Preserve that runtime dependency in the frozen ssl path.
ssl_policy = Path(sys.base_prefix) / 'Lib' / 'codex-rejected-cas.pem'
if ssl_policy.is_file():
    datas.append((str(ssl_policy), '.'))
for package in ('imageio_ffmpeg', 'faster_whisper', 'sherpa_onnx', 'reportlab', 'jsonschema', 'jsonschema_specifications'):
    datas += collect_data_files(package)
    datas += copy_metadata(package.replace('_', '-'))
binaries = [(node, 'runtime')] + collect_dynamic_libs('ctranslate2') + collect_dynamic_libs('sherpa_onnx')
a = Analysis([str(root / 'packaging/windows/launcher.py')], pathex=[str(root / 'src')],
    binaries=binaries, datas=datas,
    hiddenimports=['mathbridge.worker.__main__', 'mathbridge.providers.asr.whisper',
                   'mathbridge.providers.tts.windows', 'mathbridge.providers.translation.deepseek',
                   'mathbridge.providers.tts.gpt_sovits', 'mathbridge.providers.diarization.sherpa',
                   'mathbridge.handout.export', 'PySide6.QtSvg', 'PySide6.QtMultimedia',
                   'PySide6.QtMultimediaWidgets'],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'torch', 'tensorflow', 'pandas', 'matplotlib', 'scipy', 'IPython', 'jupyter', 'notebook'],
    noarchive=False)
# Qt uses the Windows ICU API. Do not capture a same-named Poppler ICU from the
# developer PATH; its versioned exports are incompatible with the Windows DLL.
# Windows supplies API-set forwarders as well.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != 'icuuc.dll'
              and not Path(entry[0]).name.lower().startswith('api-ms-')
              and not Path(entry[0]).name.lower().startswith('icudt78')]
# Keep Python's OpenSSL pair together. A Poppler installation on the build
# PATH can expose older same-named DLLs that break frozen HTTPS connections.
openssl = {name: Path(sys.base_prefix) / 'DLLs' / name
           for name in ('libssl-3-x64.dll', 'libcrypto-3-x64.dll')}
for name, path in openssl.items():
    if not path.is_file():
        raise RuntimeError('Python OpenSSL dependency missing: ' + name)
a.binaries = [(entry[0], str(openssl[Path(entry[0]).name.lower()]), entry[2])
              if Path(entry[0]).name.lower() in openssl else entry for entry in a.binaries]
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='课译TransLesson', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          icon=str(root / 'src/mathbridge/resources/translesson.ico'),
          version=file_version('课译TransLesson.exe'))
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='translesson-worker', debug=False,
             bootloader_ignore_signals=False, strip=False, upx=False, console=True,
             version=file_version('translesson-worker.exe'))
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name='TransLesson')
