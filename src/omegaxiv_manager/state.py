"""Local installation state for `ox`."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class InstallState:
    handle: str
    version: str
    distribution_name: str
    install_target: str
    installed_at: str
    record_url: str
    install_mode: str
    venv_path: str | None
    python_executable: str | None


class StateStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_state_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def all(self) -> list[InstallState]:
        installs = self._read().get("installs")
        if not isinstance(installs, dict):
            return []
        entries = []
        for handle, payload in installs.items():
            if not isinstance(handle, str) or not isinstance(payload, dict):
                continue
            version = _as_str(payload.get("version"))
            distribution_name = _as_str(payload.get("distribution_name"))
            install_target = _as_str(payload.get("install_target"))
            installed_at = _as_str(payload.get("installed_at"))
            record_url = _as_str(payload.get("record_url"))
            install_mode = _as_str(payload.get("install_mode")) or "global"
            venv_path = _as_str(payload.get("venv_path"))
            python_executable = _as_str(payload.get("python_executable"))
            if (
                version is None
                or distribution_name is None
                or install_target is None
                or installed_at is None
                or record_url is None
            ):
                continue
            entries.append(
                InstallState(
                    handle=handle,
                    version=version,
                    distribution_name=distribution_name,
                    install_target=install_target,
                    installed_at=installed_at,
                    record_url=record_url,
                    install_mode=install_mode,
                    venv_path=venv_path,
                    python_executable=python_executable,
                )
            )
        return sorted(entries, key=lambda item: item.handle)

    def get(self, handle: str) -> InstallState | None:
        by_handle = {entry.handle: entry for entry in self.all()}
        return by_handle.get(handle)

    def upsert(
        self,
        *,
        handle: str,
        version: str,
        distribution_name: str,
        install_target: str,
        record_url: str,
        install_mode: str,
        venv_path: str | None,
        python_executable: str | None,
    ) -> None:
        state = self._read()
        installs = state.get("installs")
        if not isinstance(installs, dict):
            installs = {}
            state["installs"] = installs
        installs[handle] = {
            "version": version,
            "distribution_name": distribution_name,
            "install_target": install_target,
            "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "record_url": record_url,
            "install_mode": install_mode,
            "venv_path": venv_path,
            "python_executable": python_executable,
        }
        self._write(state)

    def remove(self, handle: str) -> None:
        state = self._read()
        installs = state.get("installs")
        if not isinstance(installs, dict):
            return
        installs.pop(handle, None)
        self._write(state)

    def _read(self) -> dict[str, object]:
        if not self._path.exists():
            return {"installs": {}}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"installs": {}}
        return data

    def _write(self, payload: dict[str, object]) -> None:
        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _default_state_path() -> Path:
    configured = os.environ.get("OX_STATE_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".omegaxiv" / "ox-state.json"


def _as_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
