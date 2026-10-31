"""Provider switching, credential persistence, and the asynchronous worker probe."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from mathbridge.providers.translation.providers import credential_namespace, preset
from mathbridge.storage import credentials
from mathbridge.ui import api_probe, settings


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def vault(monkeypatch):
    values = {"text-api": "saved-deepseek-example-key"}
    operations = []
    monkeypatch.setattr(credentials, "load_secret", lambda scope: values.get(scope, ""))
    def save(scope, secret):
        operations.append(("save", scope)); values[scope] = secret
    def delete(scope):
        operations.append(("delete", scope)); values.pop(scope, None)
    monkeypatch.setattr(credentials, "save_secret", save)
    monkeypatch.setattr(credentials, "delete_secret", delete)
    return values, operations


def select(dialog, vendor):
    field = dialog.fields["text_provider"]
    field.setCurrentIndex(field.findData(vendor))


def test_switch_restore_and_save_independent_provider_profiles(app, vault, monkeypatch):
    values, operations = vault
    dialog = settings.SettingsDialog({**settings.DEFAULTS, "remember_api_key": True})
    assert dialog.fields["api_key"].text() == values["text-api"]
    select(dialog, "qwen")
    assert dialog.fields["api_key"].text() == ""
    assert not dialog.fields["allow_text_api"].isChecked()
    dialog.fields["api_key"].setText("qwen-example-key")
    dialog.fields["remember_api_key"].setChecked(True)
    dialog.fields["allow_text_api"].setChecked(True)
    dialog.fields["model"].setCurrentText("my-qwen-deployment")
    select(dialog, "doubao")
    assert dialog.fields["api_key"].text() == ""
    dialog.fields["model"].setCurrentText("ep-my-deployment")
    select(dialog, "qwen")
    assert dialog.fields["api_key"].text() == "qwen-example-key"
    assert dialog.fields["model"].currentText() == "my-qwen-deployment"
    assert dialog.fields["allow_text_api"].isChecked()
    result = dialog.result_values()
    profile = json.loads(result["text_api_profiles"])
    assert profile["doubao"]["model"] == "ep-my-deployment"
    assert "qwen-example-key" not in result["text_api_profiles"]
    assert "saved-deepseek-example-key" not in result["text_api_profiles"]
    assert operations == []  # Edits and provider switches do not write credentials.
    dialog.persist_credentials()
    qwen_scope = credential_namespace(result)
    assert values[qwen_scope] == "qwen-example-key"
    assert values["text-api"] == "saved-deepseek-example-key"
    persisted = {}
    class Store:
        def setValue(self, key, value): persisted[key] = value
        def sync(self): pass
    monkeypatch.setattr(settings, "QSettings", lambda *a: Store())
    settings.save_settings(result)
    assert "api_key" not in persisted
    assert not any("example-key" in str(value) for value in persisted.values())
    dialog.close()
    reopened = settings.SettingsDialog({**result, "api_key": ""})
    assert reopened.fields["api_key"].text() == "qwen-example-key"
    select(reopened, "deepseek")
    assert reopened.fields["api_key"].text() == "saved-deepseek-example-key"
    reopened.close()


def test_endpoint_change_clears_key_and_cancel_preserves_vault(app, vault):
    values, operations = vault
    dialog = settings.SettingsDialog({**settings.DEFAULTS, "remember_api_key": True})
    dialog.fields["api_base"].setText("https://proxy.example/v1")
    assert dialog.fields["api_key"].text() == ""
    dialog.fields["api_key"].setText("proxy-example-key")
    dialog.fields["api_base"].setText("https://api.deepseek.com/")
    assert dialog.fields["api_key"].text() == "saved-deepseek-example-key"
    dialog.fields["api_base"].setText("https://proxy.example/v1/chat/completions")
    assert dialog.fields["api_key"].text() == "proxy-example-key"
    dialog.reject()
    assert operations == []
    assert values == {"text-api": "saved-deepseek-example-key"}


def test_clear_saved_key_deletes_it_on_apply(app, vault):
    values, _ = vault
    dialog = settings.SettingsDialog({**settings.DEFAULTS, "remember_api_key": True})
    dialog.fields["api_key"].clear()
    dialog.persist_credentials()
    assert "text-api" not in values
    dialog.close()


def test_async_probe_uses_worker_without_project_or_course(app, tmp_path, monkeypatch):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"model": "local-contract-test", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    class Paths:
        class StandardLocation: AppLocalDataLocation = 1
        @staticmethod
        def writableLocation(_): return str(tmp_path)
    monkeypatch.setattr(api_probe, "QStandardPaths", Paths)
    probe = api_probe.ApiProbe()
    outcomes = []
    loop = QEventLoop()
    def finished(*args): outcomes.append(args); loop.quit()
    probe.finished.connect(finished)
    timer = QTimer(); timer.setSingleShot(True); timer.timeout.connect(loop.quit)
    try:
        probe.start({**settings.DEFAULTS, **preset("offline"), "text_provider": "offline",
                     "api_base": f"http://127.0.0.1:{server.server_port}/v1"})
        timer.start(20000); loop.exec()
        assert outcomes and outcomes[0][0] is True, outcomes
        assert probe.process is None  # Can immediately test another configuration.
        assert len(received) == 1
        assert json.loads(received[0]["messages"][1]["content"]) == {}
        assert "connection test" in received[0]["messages"][0]["content"]
        ledgers = list(tmp_path.rglob("api-reservations/*.json"))
        assert len(ledgers) == 1
        assert json.loads(ledgers[0].read_text())["usage"]["calls"] == 1
    finally:
        timer.stop(); probe.cancel(); server.shutdown(); server.server_close(); thread.join(2)
