# Build from project root with: .venv/Scripts/python.exe -m PyInstaller packaging/windows/MathBridge.spec
import os
import shutil
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH).resolve().parents[1]
node = os.environ.get('MATHBRIDGE_NODE') or shutil.which('node')
if not node:
    raise RuntimeError('Node.js is required to bundle offline MathJax')
handout = root / 'src' / 'mathbridge' / 'handout'
if not (handout / 'node_modules' / 'mathjax-full').exists():
    raise RuntimeError('Run handout/install-mathjax.ps1 before packaging')
datas = [
    (str(root / 'src/mathbridge/schemas'), 'mathbridge/schemas'),
    (str(handout / 'render_math.cjs'), 'mathbridge/handout'),
    (str((handout / 'node_modules/mathjax-full/js').resolve()), 'mathbridge/handout/node_modules/mathjax-full/js'),
    (str((handout / 'node_modules/mathjax-full/package.json').resolve()), 'mathbridge/handout/node_modules/mathjax-full'),
    (str((handout / 'node_modules/mathjax-full/LICENSE').resolve()), 'mathbridge/handout/node_modules/mathjax-full'),
]
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
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name='MathBridge', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
worker = EXE(pyz, a.scripts, [], exclude_binaries=True, name='mathbridge-worker', debug=False,
             bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name='MathBridge')
