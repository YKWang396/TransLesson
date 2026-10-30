"""Nonblocking local GPT-SoVITS service controls shared by application views."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QObject,QProcess,QTimer,QUrl,Signal
from PySide6.QtNetwork import QNetworkAccessManager,QNetworkRequest

from mathbridge.application.components import gpt_sovits_available
from mathbridge.providers.tts.gpt_sovits import normalize_endpoint
from mathbridge.media.engine import MediaError


def service_script():
    return Path(__file__).resolve().parents[1]/"resources/gpt-sovits-service.ps1"


def service_arguments(settings,action):
    root=Path(settings.get("gpt_sovits_path") or "")
    if not gpt_sovits_available(str(root)):
        raise ValueError("请在处理设置中选择完整的 GPT-SoVITS 文件夹。")
    endpoint=normalize_endpoint(settings.get("voice_clone_api_url") or "http://127.0.0.1:9880")
    address=urlsplit(endpoint)
    return ["-NoProfile","-ExecutionPolicy","Bypass","-File",str(service_script()),
            "-BundleRoot",str(root.resolve()),"-Action",action,"-Address",address.hostname,
            "-Port",str(address.port or 80)]


def _process_alive(identifier):
    if os.name!='nt' or not identifier:return True
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,int(identifier))
    if not handle:return ctypes.get_last_error()==5
    try:
        status=wintypes.DWORD()
        return not kernel.GetExitCodeProcess(handle,ctypes.byref(status)) or status.value==259
    finally:kernel.CloseHandle(handle)


class LocalVoiceService(QObject):
    changed=Signal(str,str)
    busyChanged=Signal(bool)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.status="unknown";self.message="声音服务未检查"
        self.process=None;self.reply=None;self.settings={};self.pending=False
        self.network=QNetworkAccessManager(self)
        self.poll=QTimer(self);self.poll.setInterval(2000);self.poll.timeout.connect(self._probe)
        self.timeout=QTimer(self);self.timeout.setSingleShot(True);self.timeout.timeout.connect(self._abort_probe)
        self.attempts=0;self.owned_pid=None;self.external=False

    def _set(self,status,message):
        if (self.status,self.message)==(status,message):return
        self.status,self.message=status,message
        self.changed.emit(status,message)

    def start(self,settings):self._command(settings,"start")
    def stop(self,settings):self._command(settings,"stop")

    def _command(self,settings,action):
        if self.process is not None:return
        try:args=service_arguments(settings,action)
        except (ValueError,OSError,MediaError) as error:
            self._set("error",str(error));return
        self.settings={k:settings.get(k) for k in ("gpt_sovits_path","voice_clone_api_url")}
        self.poll.stop();self.attempts=0;self.pending=False
        self._reset_probe()
        self.process=QProcess(self)
        self.process.finished.connect(lambda code,_status:self._command_done(action,code))
        self.process.errorOccurred.connect(self._process_error)
        self.busyChanged.emit(True)
        self._set("starting" if action=="start" else "stopping","正在启动声音服务…" if action=="start" else "正在停止声音服务…")
        powershell=Path(os.environ.get("WINDIR","C:/Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
        self.process.start(str(powershell),args)

    def _process_error(self,error):
        if error==QProcess.ProcessError.FailedToStart:
            self._cleanup_process();self._set("error","无法启动服务管理程序，请检查 Windows PowerShell。")

    def _cleanup_process(self):
        if self.process is not None:self.process.deleteLater();self.process=None
        self.busyChanged.emit(False)

    def _command_done(self,action,code):
        if self.process is None:return
        raw=bytes(self.process.readAllStandardOutput()).decode("utf-8-sig",errors="replace").strip()
        try:data=json.loads(raw)
        except ValueError:data={"status":"error","message":"服务启动失败，请打开服务日志查看原因。"}
        self._cleanup_process()
        if code or data.get("status")=="error":
            self._set("error",data.get("message","服务操作失败"));return
        if action=="stop":
            self._set("stopped" if data.get("status")=="stopped" else "external",
                      "声音服务已停止" if data.get("status")=="stopped" else "没有由本应用启动的服务；现有外部服务未停止。")
            return
        self.owned_pid=data.get('pid');self.external=data.get('status')=='external'
        self.pending=True;self.poll.start();self._probe()

    def check(self,settings):
        self.settings={k:settings.get(k) for k in ("gpt_sovits_path","voice_clone_api_url")}
        if self.process is None:self._probe()

    def _reset_probe(self):
        self.timeout.stop()
        if self.reply is not None:
            reply=self.reply;self.reply=None
            reply.finished.disconnect(self._probe_done);reply.abort();reply.deleteLater()

    def _probe(self):
        if self.reply is not None:return
        try:endpoint=normalize_endpoint(self.settings.get("voice_clone_api_url") or "http://127.0.0.1:9880")
        except (ValueError,MediaError) as error:self._set("error",str(error));return
        self.reply=self.network.get(QNetworkRequest(QUrl(endpoint+"/openapi.json")))
        self.reply.finished.connect(self._probe_done)
        self.timeout.start(2500)

    def _abort_probe(self):
        if self.reply is not None:self.reply.abort()

    def _probe_done(self):
        if self.reply is None:return
        self.timeout.stop();reply=self.reply;self.reply=None
        data=bytes(reply.readAll())[:2_000_000];reply.deleteLater()
        try:ready=bool(json.loads(data)["paths"]["/tts"]["post"]["requestBody"])
        except (ValueError,KeyError,TypeError):ready=False
        if ready:
            self.pending=False;self.poll.stop();self._set("ready","声音服务已就绪")
        elif self.pending:
            self.attempts+=1
            if self.external:
                self.pending=False;self.poll.stop();self._set('error','端口已占用，但未识别到 GPT-SoVITS 接口。请检查服务地址。')
            elif self.owned_pid and not _process_alive(self.owned_pid):
                self.pending=False;self.poll.stop();self._set('error','声音服务启动失败，请打开日志查看原因。')
            elif self.attempts>=90:
                self.pending=False;self.poll.stop();self._set("error","声音服务尚未就绪，请打开日志检查模型加载情况。")
            else:self._set("starting","正在加载声音模型…")
        else:self._set("stopped","声音服务未启动")
