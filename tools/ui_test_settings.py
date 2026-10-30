"""Keep UI smoke tests out of the user's settings and Windows credentials."""
def isolate_settings(directory):
    from pathlib import Path
    from PySide6.QtCore import QSettings
    import mathbridge.ui.main_window as ui
    import mathbridge.ui.settings as preferences
    path = str(Path(directory)/"ui-settings.ini")
    def settings(*args):
        return QSettings(path, QSettings.Format.IniFormat)
    ui.QSettings = preferences.QSettings = settings
