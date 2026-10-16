from youtube_ready import ai_runtime


def test_ensure_ollama_runtime_reports_missing_binary(monkeypatch):
    monkeypatch.setattr(ai_runtime.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ai_runtime, "_ollama_running", lambda: False)

    status = ai_runtime.ensure_ai_runtime("ollama", "llama3", start=True)

    assert status.available is False
    assert status.running is False
    assert "not installed" in status.message


def test_ensure_ollama_runtime_starts_server(monkeypatch):
    calls = {"running": 0, "popen": 0}

    def fake_running():
        calls["running"] += 1
        return calls["running"] > 1

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(*_args, **_kwargs):
        calls["popen"] += 1
        return FakeProcess()

    monkeypatch.setattr(ai_runtime.shutil, "which", lambda _name: "/usr/local/bin/ollama")
    monkeypatch.setattr(ai_runtime, "_ollama_running", fake_running)
    monkeypatch.setattr(ai_runtime.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ai_runtime.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ai_runtime, "_ollama_models", lambda: ["llama3"])
    monkeypatch.setattr(ai_runtime, "_OLLAMA_PROCESS", None)

    status = ai_runtime.ensure_ai_runtime("ollama", "llama3", start=True)

    assert status.running is True
    assert status.started is True
    assert status.model_available is True
    assert calls["popen"] == 1


def test_ensure_mlx_runtime_loads_requested_model(monkeypatch):
    loaded = []
    monkeypatch.setattr(ai_runtime, "_load_mlx", lambda model: loaded.append(model))

    status = ai_runtime.ensure_ai_runtime("mlx", "mlx-community/test", start=True)

    assert status.running is True
    assert status.model_available is True
    assert loaded == ["mlx-community/test"]
