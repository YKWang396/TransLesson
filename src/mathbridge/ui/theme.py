"""The application's compact, native widget theme."""

STYLESHEET = """
QMainWindow, QDialog { background: #f5f7fb; }
QWidget { color: #233047; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 10pt; }
QLabel#brand { color: #172b4d; font-size: 19pt; font-weight: 700; }
QLabel#eyebrow { color: #687a95; font-size: 9pt; }
QLabel#pageTitle { font-size: 20pt; font-weight: 700; color: #172b4d; }
QFrame#reviewGuide { background: #eaf0fc; border: 1px solid #d8e3f7; border-radius: 8px; }
QLabel#guideTitle { color: #203e70; font-size: 13pt; font-weight: 700; }
QLabel#description { color: #65738a; }
QLabel#saveStatus { color: #34755b; padding: 5px 10px; border-radius: 10px; background: #e7f3ec; }
QLabel#notice { color: #775e2a; background: #fff8e7; padding: 10px; border-radius: 6px; }
QGroupBox { background: white; border: 1px solid #dfe5ef; border-radius: 8px; margin-top: 18px; padding: 14px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton { background: #fff; border: 1px solid #d7dfeb; border-radius: 6px; padding: 7px 12px; min-height: 20px; }
QPushButton:hover { background: #edf3ff; border-color: #93ace0; }
QPushButton:pressed { background: #dce8ff; }
QPushButton:disabled { color: #9ba5b4; background: #f1f3f7; border-color: #e3e7ed; }
QPushButton#primary { background: #3566d6; color: white; border-color: #3566d6; font-weight: 600; }
QPushButton#primary:hover { background: #2855ba; }
QPushButton#primary:disabled { background: #a9bce9; border-color: #a9bce9; }
QPushButton#danger { color: #ad4c45; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #d8e0eb; border-radius: 5px; padding: 6px; selection-background-color: #cbdcff; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #6c91e4; }
QLineEdit:disabled, QPlainTextEdit:disabled { background: #eef1f6; color: #8b97a9; }
QTabWidget::pane { border: none; margin-top: 8px; }
QTabBar::tab { background: transparent; color: #61708a; padding: 10px 23px; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: #2c5fc8; border-bottom-color: #3566d6; font-weight: 600; }
QTabBar::tab:hover { background: #eaf0fb; }
QTableWidget, QListWidget, QTextBrowser { background: white; border: 1px solid #e0e5ee; border-radius: 6px; alternate-background-color: #f7f9fd; selection-background-color: #e4edff; selection-color: #183a79; }
QTableWidget::item { padding: 8px; border-bottom: 1px solid #eef1f6; }
QHeaderView::section { background: #edf1f8; color: #60708b; border: none; padding: 9px; font-weight: 600; }
QListWidget::item { padding: 9px; }
QListWidget::item:selected { background: #e4edff; color: #183a79; }
QProgressBar { border: none; background: #e4e9f2; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #4675db; border-radius: 3px; }
QSplitter::handle { background: transparent; width: 8px; }
QToolTip { background: white; color: #233047; border: 1px solid #d8e0eb; padding: 5px; }
QStatusBar { background: #edf1f8; }
QSlider::groove:horizontal { height: 4px; background: #d6deed; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; margin: -4px 0; background: #4774d5; border-radius: 6px; }
"""
