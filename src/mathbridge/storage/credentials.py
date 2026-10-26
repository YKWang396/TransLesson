"""Windows Credential Manager; never serialize API keys into a project."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes


class Credential(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _api():
    if os.name != "nt":
        raise OSError("仅 Windows 支持系统凭据库；可仅在本次会话输入 Key")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredFree.argtypes = [ctypes.c_void_p]
    return api


def save_secret(namespace: str, secret: str) -> None:
    blob = secret.encode("utf-16-le")
    data = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    item = Credential(Type=1, TargetName="MathBridge/" + namespace, CredentialBlobSize=len(blob),
                      CredentialBlob=data, Persist=2, UserName="MathBridge")
    if not _api().CredWriteW(ctypes.byref(item), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def load_secret(namespace: str) -> str:
    api = _api()
    result = ctypes.POINTER(Credential)()
    if not api.CredReadW("MathBridge/" + namespace, 1, 0, ctypes.byref(result)):
        if ctypes.get_last_error() == 1168:
            return ""
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.contents.CredentialBlob, result.contents.CredentialBlobSize).decode("utf-16-le")
    finally:
        api.CredFree(result)


def delete_secret(namespace: str) -> None:
    api = _api()
    if not api.CredDeleteW("MathBridge/" + namespace, 1, 0) and ctypes.get_last_error() != 1168:
        raise ctypes.WinError(ctypes.get_last_error())
