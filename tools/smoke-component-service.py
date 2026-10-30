"""Exercise the real nonblocking service controls, leaving the service running."""
from pathlib import Path
import argparse
import json
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QEventLoop,QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont,QFontDatabase
from mathbridge.ui.voice_service import LocalVoiceService
from mathbridge.ui.settings import SettingsDialog,load_settings
from mathbridge.ui.theme import STYLESHEET


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real',action='store_true',required=True)
    args=parser.parse_args()
    app=QApplication([])
    for name in ('msyh.ttc','msyhbd.ttc'):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name))
    app.setFont(QFont('Microsoft YaHei UI',10));app.setStyleSheet(STYLESHEET)
    manager=LocalVoiceService();settings=load_settings()
    def wait_for(action,wanted):
        loop=QEventLoop();state=[]
        def changed(status,message):
            print(status,message,flush=True);state.append(status)
            if status in wanted or status=='error':loop.quit()
        manager.changed.connect(changed);action()
        deadline=QTimer();deadline.setSingleShot(True);deadline.timeout.connect(loop.quit);deadline.start(90000)
        if not state or state[-1] not in wanted|{'error'}:loop.exec()
        manager.changed.disconnect(changed)
        assert state and state[-1] in wanted,state
    wait_for(lambda:manager.start(settings),{'ready'})
    port=9880;root=Path(settings['gpt_sovits_path']);state=root/f'output/translesson-api-{port}.json'
    initial=json.loads(state.read_text('utf-8-sig'))['pid']
    wait_for(lambda:manager.start(settings),{'ready'})
    assert json.loads(state.read_text('utf-8-sig'))['pid']==initial
    wait_for(lambda:manager.stop(settings),{'stopped'})
    wait_for(lambda:manager.start(settings),{'ready'})
    dialog=SettingsDialog(settings,service_manager=manager)
    assert '已找到' in dialog.component_status.text()
    dialog.resize(760,700);dialog.show();app.processEvents()
    (ROOT/'artifacts').mkdir(exist_ok=True);dialog.grab().save(str(ROOT/'artifacts/ui-components-019.png'))
    print('PASS: real bundled script start, ready, idempotence, owned stop, restart; service left running',flush=True)


if __name__=='__main__':main()
