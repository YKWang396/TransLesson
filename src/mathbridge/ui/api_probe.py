"""Small asynchronous JSON contract test; sends no course or video content."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import uuid

from PySide6.QtCore import QObject,QProcess,QProcessEnvironment,QStandardPaths,Signal


class ApiProbe(QObject):
    finished = Signal(bool,str)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.process=None
        self.buffer=b""
        self.done=False
        self.outcome=None

    def start(self,settings):
        if self.process is not None:return
        request_id="req-api-test-"+uuid.uuid4().hex
        # The request ID already makes the ledger unique. Avoid repeating it in
        # the folder path, which exceeded Windows path limits under long roots.
        root=Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/"api-tests"
        root.mkdir(parents=True,exist_ok=True)
        values=dict(settings,allow_text_api=True,max_output_tokens=1024,max_calls=2,
                    _request_id=request_id,_project_dir=str(root),usage_spent_cny=0,usage_calls=0,timeout=30)
        command=dict(stage="test_api",request_id=request_id,project_dir=str(root),settings=values)
        self.buffer=b"";self.done=False;self.outcome=None
        self.process=QProcess(self)
        environment=QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING","utf-8")
        environment.insert("PYTHONUTF8","1")
        environment.insert("PYTHONPATH",str(Path(__file__).resolve().parents[2]))
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(root))
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._ended)
        self.process.errorOccurred.connect(self._error)
        self.process.started.connect(lambda:self.process.write((json.dumps(command,ensure_ascii=False)+"\n").encode()))
        if getattr(sys,"frozen",False):
            self.process.start(str(Path(sys.executable).with_name("translesson-worker.exe")),["--worker"])
        else:self.process.start(sys.executable,["-m","mathbridge.worker"])

    def _read(self):
        self.buffer+=bytes(self.process.readAllStandardOutput())
        while b"\n" in self.buffer:
            raw,self.buffer=self.buffer.split(b"\n",1)
            try:message=json.loads(raw)
            except (ValueError,UnicodeDecodeError):continue
            if message.get("type")!="done" or self.done:continue
            self.done=True
            if message.get("status")=="succeeded":
                data=message["result"]["metadata"]
                amount=data["usage"]["cost_cny"]
                self.outcome=(True,f"连接及 JSON 测试通过 · {data['model']} · 估算 ¥{amount:.4f}")
            else:self.outcome=(False,message.get("message","测试失败"))

    def _ended(self,*_):
        if self.process is None:return
        self._read()
        if not self.done:self.outcome=(False,"测试进程意外退出，请检查接口设置。")
        self.process.deleteLater();self.process=None
        if self.outcome:self.finished.emit(*self.outcome)

    def _error(self,error):
        if error==QProcess.ProcessError.FailedToStart:
            self.done=True
            self.finished.emit(False,"无法启动接口测试进程。")
            self.process.deleteLater();self.process=None

    def cancel(self):
        self.done=True;self.outcome=None
        if self.process is not None:
            self.process.kill()
            self.process.waitForFinished(1000)
