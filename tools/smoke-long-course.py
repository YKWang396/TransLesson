"""Measure review edits on a supplied long course using an isolated copy."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import time
import uuid

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QFontDatabase
from mathbridge.application.coordinator import Coordinator
from mathbridge.storage.project_store import ProjectStore,atomic_json
from mathbridge.ui.main_window import MainWindow
from ui_test_settings import isolate_settings


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project',type=Path,required=True)
    args=parser.parse_args();source=args.project.resolve();original=(source/'project.json').read_bytes()
    project=json.loads(original)
    assert len(project['segments'])>=100,'Provide a course with at least 100 segments'
    root=ROOT/'tmp'/('long-course-'+uuid.uuid4().hex[:10]);root.mkdir()
    ProjectStore(root).save(project);isolate_settings(root)
    app=QApplication([])
    for name in ('msyh.ttc','msyhbd.ttc'):
        QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts'/name))
    coordinator=Coordinator();window=MainWindow(coordinator);window.show();window.resize(1120,760)
    started=time.perf_counter();coordinator.open_project(root);window.tabs.setCurrentIndex(1);QCoreApplication.processEvents()
    timings=dict(load_seconds=round(time.perf_counter()-started,3),segments=len(project['segments']))
    table=window.segment_table
    assert table.rowCount()==len(project['segments'])
    items=[table.item(r,c) for r in range(table.rowCount()) for c in range(table.columnCount())]
    segment=next(s for s in project['segments'] if not s['formula_ids'] and s['subtitle_plain'].strip())
    window._select_segment(segment['id']);QCoreApplication.processEvents()
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum()//2)
    scroll=table.verticalScrollBar().value();revision=coordinator.project['revision']
    window.segment_fields['subtitle_plain'].setPlainText(segment['subtitle_plain']+'（保存检查）')
    started=time.perf_counter();assert window._commit_all();QCoreApplication.processEvents()
    timings['save_seconds']=round(time.perf_counter()-started,3)
    assert coordinator.project['revision']==revision+1
    assert all(table.item(r,c) is items[r*table.columnCount()+c] for r in range(table.rowCount()) for c in range(table.columnCount()))
    assert table.verticalScrollBar().value()==scroll
    coordinator.undo();assert ProjectStore(root).load()['segments']==project['segments']
    started=time.perf_counter();coordinator.bulk_confirm_segments();QCoreApplication.processEvents()
    timings['bulk_seconds']=round(time.perf_counter()-started,3)
    assert coordinator.project['formulas']==project['formulas']
    assert hashlib.sha256((source/'project.json').read_bytes()).digest()==hashlib.sha256(original).digest()
    atomic_json(ROOT/'artifacts/long-course-022.json',timings)
    window.grab().save(str(ROOT/'artifacts/ui-long-course-022.png'));window.close()
    print('PASS long course edit/save/undo/bulk confirmation/item reuse/scroll preservation',json.dumps(timings))


if __name__=='__main__':main()
