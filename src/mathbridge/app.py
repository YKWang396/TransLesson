from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--worker" in arguments:
        from mathbridge.worker.__main__ import main as worker_main
        return worker_main()
    parser = argparse.ArgumentParser(description="课译 MathBridge · 本地数学课程翻译工作台")
    parser.add_argument("project", nargs="?", help="要打开的项目目录")
    parser.add_argument("--smoke-test", action="store_true", help="启动并检查原生界面后退出")
    parser.add_argument("--screenshot", help="启动后保存一张窗口截图")
    args = parser.parse_args(arguments)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("MathBridge")
    app.setApplicationName("MathBridge")
    from mathbridge import __version__
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    # Qt's offscreen platform does not enumerate Windows system fonts. Loading
    # the installed font explicitly also keeps automated visual QA legible.
    if not QFontDatabase.families():
        font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
            if (font_root / filename).is_file():
                QFontDatabase.addApplicationFont(str(font_root / filename))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    from mathbridge.application.coordinator import Coordinator
    from mathbridge.ui.main_window import MainWindow
    coordinator = Coordinator()
    window = MainWindow(coordinator)
    window.show()
    if args.project:
        QTimer.singleShot(50, lambda: coordinator.open_project(args.project))
    if args.screenshot:
        def capture():
            output = Path(args.screenshot)
            output.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(output))
        QTimer.singleShot(600, capture)
    if args.smoke_test:
        QTimer.singleShot(1100, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
