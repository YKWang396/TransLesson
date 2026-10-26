"""Read-only render of generated integration PDFs for visual inspection."""
import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
app = QApplication([])
report = json.loads((ROOT / 'artifacts/pipeline-report.json').read_text(encoding='utf-8'))
for result in report['results']:
    pdf = next(Path(p) for p in result['outputs'] if Path(p).suffix == '.pdf')
    document = QPdfDocument()
    data = QBuffer()
    data.setData(QByteArray(pdf.read_bytes()))
    assert data.open(QIODevice.OpenModeFlag.ReadOnly)
    document.load(data)
    assert document.error() == QPdfDocument.Error.None_, document.error()
    for page in range(document.pageCount()):
        size = document.pagePointSize(page)
        pixels = QSize(round(size.width() * 150 / 72), round(size.height() * 150 / 72))
        image = document.render(page, pixels)
        background = QImage(pixels, QImage.Format.Format_RGB32)
        background.fill(Qt.GlobalColor.white)
        painter = QPainter(background)
        painter.drawImage(0, 0, image)
        painter.end()
        target = ROOT / 'artifacts' / (result['direction'].replace(' -> ', '-to-') + f'-handout-{page + 1}.png')
        assert background.save(str(target))
        print(target)
