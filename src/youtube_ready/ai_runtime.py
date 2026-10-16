from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass

from youtube_ready.metadata import _load_mlx, list_models

_OLLAMA_PROCESS: subprocess.Popen | None = None


@dataclass
class AIRuntimeStatus:
    backend: str
    available: bool
    running: bool
    started: bool = False
    model: str | None = None
    model_available: bool | None = None
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "available": self.available,
            "running": self.running,
            "started": self.started,
            "model": self.model,
            "model_available": self.model_available,
            "message": self.message,
        }


def _ollama_models() -> list[str]:
    try:
        return list_models("ollama")
    except Exception:
        return []


def _ollama_running() -> bool:
    return bool(_ollama_models())


def _start_ollama(timeout: float = 8.0) -> AIRuntimeStatus:
    global _OLLAMA_PROCESS
    if _ollama_running():
        return AIRuntimeStatus("ollama", available=True, running=True, message="Ollama is already running.")
    if shutil.which("ollama") is None:
        return AIRuntimeStatus("ollama", available=False, running=False, message="Ollama is not installed or not on PATH.")

    if _OLLAMA_PROCESS is None or _OLLAMA_PROCESS.poll() is not None:
        _OLLAMA_PROCESS = subprocess.Popen(  # noqa: S603 - local user runtime, no shell
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ollama_running():
            return AIRuntimeStatus("ollama", available=True, running=True, started=True, message="Ollama started.")
        time.sleep(0.25)
    return AIRuntimeStatus("ollama", available=True, running=False, started=True, message="Ollama start was requested but is not responding yet.")


def ensure_ai_runtime(backend: str, model: str | None = None, *, start: bool = True) -> AIRuntimeStatus:
    if backend == "ollama":
        status = _start_ollama() if start else AIRuntimeStatus("ollama", available=shutil.which("ollama") is not None, running=_ollama_running())
        if model:
            models = _ollama_models() if status.running else []
            status.model = model
            status.model_available = model in models
            if status.running and not status.model_available:
                status.message = f"Ollama is running, but model {model!r} is not pulled."
        return status

    if backend == "mlx":
        if not model:
            try:
                models = list_models("mlx")
            except Exception as exc:
                return AIRuntimeStatus("mlx", available=False, running=False, message=str(exc))
            return AIRuntimeStatus("mlx", available=True, running=bool(models), model_available=None, message=f"{len(models)} cached MLX model(s) found.")
        try:
            if start:
                _load_mlx(model)
            return AIRuntimeStatus("mlx", available=True, running=True, model=model, model_available=True, started=start, message=f"MLX model {model!r} is ready.")
        except Exception as exc:
            return AIRuntimeStatus("mlx", available=False, running=False, model=model, model_available=False, message=str(exc))

    return AIRuntimeStatus(backend, available=False, running=False, model=model, message=f"Unknown AI backend {backend!r}.")
