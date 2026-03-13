"""CLI entrypoint for `ox` package manager."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from omegaxiv_manager.registry import RegistryClient
from omegaxiv_manager.state import InstallState, StateStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="ox", description="omegaXiv package manager.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install a package handle.")
    install.add_argument("spec", help="Handle or handle==version.")
    _add_mode_args(install)

    upgrade = subparsers.add_parser("upgrade", help="Upgrade to the latest version.")
    upgrade.add_argument("handle")
    _add_mode_args(upgrade)

    uninstall = subparsers.add_parser("uninstall", help="Uninstall a previously installed handle.")
    uninstall.add_argument("handle")

    subparsers.add_parser("list", help="List locally tracked installs.")

    search = subparsers.add_parser("search", help="Search handles in registry.")
    search.add_argument("query")

    show = subparsers.add_parser("show", help="Show handle metadata.")
    show.add_argument("handle")

    args = parser.parse_args()
    registry = RegistryClient()
    state = StateStore()
    try:
        _run_command(args, registry, state)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _run_command(args: argparse.Namespace, registry: RegistryClient, state: StateStore) -> None:

    if args.command == "install":
        handle, version = _parse_spec(args.spec)
        resolved = registry.resolve(handle, version)
        existing = state.get(resolved.handle)
        install_mode = _resolve_install_mode(
            package_type=resolved.package_type,
            existing=existing,
            isolated=args.isolated,
            global_install=args.global_install,
        )
        python_executable, venv_path, pip_command = _resolve_install_context(
            handle=resolved.handle,
            install_mode=install_mode,
            python_override=args.python_executable,
            existing=existing,
        )
        _pip_install(
            pip_command,
            resolved.install_target,
            upgrade=False,
            requirements_url=resolved.requirements_url,
        )
        state.upsert(
            handle=resolved.handle,
            version=resolved.version,
            distribution_name=resolved.distribution_name,
            install_target=resolved.install_target,
            record_url=resolved.record_url,
            install_mode=install_mode,
            venv_path=venv_path,
            python_executable=python_executable,
        )
        print(_install_summary("installed", resolved.handle, resolved.version, install_mode, venv_path))
        return

    if args.command == "upgrade":
        resolved = registry.resolve(args.handle, None)
        existing = state.get(resolved.handle) or state.get(args.handle)
        install_mode = _resolve_install_mode(
            package_type=resolved.package_type,
            existing=existing,
            isolated=args.isolated,
            global_install=args.global_install,
        )
        python_executable, venv_path, pip_command = _resolve_install_context(
            handle=resolved.handle,
            install_mode=install_mode,
            python_override=args.python_executable,
            existing=existing,
        )
        _pip_install(
            pip_command,
            resolved.install_target,
            upgrade=True,
            requirements_url=resolved.requirements_url,
        )
        state.upsert(
            handle=resolved.handle,
            version=resolved.version,
            distribution_name=resolved.distribution_name,
            install_target=resolved.install_target,
            record_url=resolved.record_url,
            install_mode=install_mode,
            venv_path=venv_path,
            python_executable=python_executable,
        )
        print(_install_summary("upgraded", resolved.handle, resolved.version, install_mode, venv_path))
        return

    if args.command == "uninstall":
        installed = state.get(args.handle)
        if installed is None:
            raise SystemExit(f"handle is not installed: {args.handle}")
        _pip_uninstall(_pip_command_for_install(installed), installed.distribution_name)
        if installed.install_mode == "isolated" and installed.venv_path:
            shutil.rmtree(Path(installed.venv_path).expanduser(), ignore_errors=True)
        state.remove(args.handle)
        print(f"uninstalled {args.handle}")
        return

    if args.command == "list":
        installs = state.all()
        if not installs:
            print("no tracked installs")
            return
        for item in installs:
            print(_list_summary(item))
        return

    if args.command == "search":
        matches = registry.search(args.query)
        if not matches:
            print("no matches")
            return
        for match in matches:
            print(f"{match.handle} {match.latest_version} [{match.package_type}]")
            if match.summary:
                print(f"  {match.summary}")
        return

    if args.command == "show":
        payload = registry.show(args.handle)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return


def _add_mode_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="Install in a managed virtual environment.",
    )
    parser.add_argument(
        "--global-install",
        dest="global_install",
        action="store_true",
        help="Install into the current Python environment.",
    )
    parser.add_argument(
        "--python",
        dest="python_executable",
        help="Python executable to use when creating an isolated environment.",
    )


def _parse_spec(value: str) -> tuple[str, str | None]:
    handle, separator, version = value.partition("==")
    handle = handle.strip()
    if not handle:
        raise SystemExit("handle is required")
    if not separator:
        return handle, None
    version = version.strip()
    if not version:
        raise SystemExit("version cannot be empty")
    return handle, version


def _resolve_install_mode(
    *,
    package_type: str,
    existing: InstallState | None,
    isolated: bool,
    global_install: bool,
) -> str:
    if isolated and global_install:
        raise SystemExit("choose either --isolated or --global-install")
    if isolated:
        return "isolated"
    if global_install:
        return "global"
    if existing:
        return existing.install_mode
    return "global" if package_type == "library" else "isolated"


def _resolve_install_context(
    *,
    handle: str,
    install_mode: str,
    python_override: str | None,
    existing: InstallState | None,
) -> tuple[str | None, str | None, list[str]]:
    if install_mode == "global":
        return None, None, [sys.executable, "-m", "pip"]
    python_executable = (
        (python_override or "").strip()
        or (existing.python_executable if existing else "")
        or os.environ.get("OX_PYTHON_BIN", "").strip()
        or sys.executable
    )
    venv_path = (
        Path(existing.venv_path).expanduser()
        if existing and existing.venv_path
        else _default_venv_path(handle)
    )
    _ensure_venv(venv_path, python_executable)
    return python_executable, str(venv_path), [str(_venv_script(venv_path, "pip"))]


def _default_venv_path(handle: str) -> Path:
    return Path.home() / ".omegaxiv" / "envs" / handle


def _venv_script(venv_path: Path, name: str) -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    ext = ".exe" if os.name == "nt" else ""
    return venv_path / folder / f"{name}{ext}"


def _ensure_venv(venv_path: Path, python_executable: str) -> None:
    python_path = _venv_script(venv_path, "python")
    if not python_path.exists():
        venv_path.parent.mkdir(parents=True, exist_ok=True)
        _run([python_executable, "-m", "venv", str(venv_path)])
    pip_path = _venv_script(venv_path, "pip")
    if not pip_path.exists():
        raise SystemExit(f"isolated environment is missing pip: {venv_path}")


def _pip_command_for_install(installed: InstallState) -> list[str]:
    if installed.install_mode != "isolated" or not installed.venv_path:
        return [sys.executable, "-m", "pip"]
    return [str(_venv_script(Path(installed.venv_path).expanduser(), "pip"))]


def _pip_install(
    pip_command: list[str],
    target: str,
    *,
    upgrade: bool,
    requirements_url: str | None,
) -> None:
    if requirements_url:
        _run([*pip_command, "install", "-r", requirements_url])
    command = [*pip_command, "install"]
    if upgrade:
        command.append("--upgrade")
    command.append(target)
    _run(command)


def _pip_uninstall(pip_command: list[str], distribution_name: str) -> None:
    command = [*pip_command, "uninstall", "-y", distribution_name]
    _run(command)


def _list_summary(installed: InstallState) -> str:
    if installed.install_mode == "isolated":
        suffix = f", isolated @ {installed.venv_path}" if installed.venv_path else ", isolated"
        return f"{installed.handle}=={installed.version} ({installed.distribution_name}{suffix})"
    return f"{installed.handle}=={installed.version} ({installed.distribution_name}, global)"


def _install_summary(
    action: str,
    handle: str,
    version: str,
    install_mode: str,
    venv_path: str | None,
) -> str:
    if install_mode == "isolated":
        location = f"isolated @ {venv_path}" if venv_path else "isolated"
        return f"{action} {handle}=={version} ({location})"
    return f"{action} {handle}=={version} (global)"


def _run(command: list[str]) -> None:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
