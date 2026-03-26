"""CLI entrypoint for `ox` package manager."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from omegaxiv_manager.registry import RegistryClient, ResolvedPackage
from omegaxiv_manager.state import InstallState, StateStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="ox", description="omegaXiv package manager.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install a package handle.")
    install.add_argument("spec", help="Handle or handle==version.")
    install.add_argument(
        "--mcp",
        dest="mcp_target",
        nargs="?",
        const="all",
        choices=("codex", "claude", "all"),
        help="Also register the packaged MCP server for Codex, Claude, or both.",
    )
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
        mcp_target = getattr(args, "mcp_target", None)
        skip_package_install = (
            mcp_target is not None
            and existing is not None
            and existing.version == resolved.version
        )
        messages = []
        if skip_package_install:
            installed = existing
            assert installed is not None
            messages.append(
                _install_summary(
                    "reused",
                    installed.handle,
                    installed.version,
                    installed.install_mode,
                    installed.venv_path,
                )
            )
        else:
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
            installed = state.get(resolved.handle)
            assert installed is not None
            messages.append(
                _install_summary(
                    "installed",
                    resolved.handle,
                    resolved.version,
                    install_mode,
                    venv_path,
                )
            )
        if mcp_target is not None:
            messages.extend(_install_mcp_targets(registry, resolved, installed, mcp_target))
        print("\n".join(messages))
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
        req_command = [*pip_command, "install", "-r", requirements_url]
        if upgrade:
            req_command.insert(-2, "--upgrade")
        _run(req_command)
    command = [*pip_command, "install"]
    if upgrade:
        command.append("--upgrade")
    command.append(target)
    _run(command)


def _pip_uninstall(pip_command: list[str], distribution_name: str) -> None:
    command = [*pip_command, "uninstall", "-y", distribution_name]
    _run(command)


def _install_mcp_targets(
    registry: RegistryClient,
    resolved: ResolvedPackage,
    installed: InstallState,
    target_selector: str,
) -> list[str]:
    try:
        manifest = registry.load_manifest(resolved.manifest_url)
    except ValueError as exc:
        return [f"MCP registration skipped: could not load packaging manifest ({exc})."]
    config = _resolve_mcp_config(resolved.handle, manifest, installed)
    if config is None:
        return ["MCP registration skipped: package manifest does not declare an MCP server."]
    summaries = []
    for target in _selected_mcp_targets(target_selector):
        try:
            if target == "codex":
                path, changed = _install_codex_mcp_server(config)
            else:
                path, changed = _install_claude_mcp_server(config)
        except ValueError as exc:
            summaries.append(f"MCP registration skipped for {target}: {exc}")
            continue
        status = "updated" if changed else "already current"
        summaries.append(f"MCP {status} for {target} at {path}")
    return summaries


def _resolve_mcp_config(
    handle: str,
    manifest: dict[str, object] | None,
    installed: InstallState,
) -> dict[str, object] | None:
    if manifest is None:
        return None
    raw = manifest.get("mcp")
    if not isinstance(raw, dict):
        return None
    server_name = _as_str(raw.get("server_name")) or handle
    transport = _as_str(raw.get("transport")) or "stdio"
    if transport != "stdio":
        raise ValueError("only stdio MCP manifests are supported")
    entrypoint = raw.get("entrypoint")
    resolved_entrypoint = _resolve_mcp_entrypoint(manifest, entrypoint, installed)
    if resolved_entrypoint is None:
        raise ValueError("manifest MCP entrypoint is missing a runnable module or command")
    command, args = resolved_entrypoint
    config = {
        "server_name": server_name,
        "command": command,
        "args": args,
        "env": _string_dict(raw.get("env")),
        "cwd": _as_str(raw.get("cwd")),
        "startup_timeout_sec": _number_value(raw.get("startup_timeout_sec")),
        "tool_timeout_sec": _number_value(raw.get("tool_timeout_sec")),
    }
    return config


def _resolve_mcp_entrypoint(
    manifest: dict[str, object],
    entrypoint: object,
    installed: InstallState,
) -> tuple[str, list[str]] | None:
    python_command = _mcp_python_command(installed)
    if isinstance(entrypoint, dict):
        module = _as_str(entrypoint.get("module"))
        kind = _as_str(entrypoint.get("kind")) or "python_module"
        extra_args = _string_list(entrypoint.get("args"))
        if module is not None and kind == "python_module":
            return python_command, ["-m", module, *extra_args]
        command = _as_str(entrypoint.get("command"))
        if command is not None:
            if command in {"python", "python3"} and len(extra_args) >= 2 and extra_args[0] == "-m":
                return python_command, extra_args
            return command, extra_args
    import_name = _as_str(manifest.get("import_name"))
    if import_name is None:
        return None
    return python_command, ["-m", f"{import_name}.mcp_server"]


def _mcp_python_command(installed: InstallState) -> str:
    if installed.install_mode == "isolated":
        assert installed.venv_path is not None
        return str(_venv_script(Path(installed.venv_path).expanduser(), "python"))
    return sys.executable


def _selected_mcp_targets(value: str) -> tuple[str, ...]:
    if value == "all":
        return ("codex", "claude")
    return (value,)


def _install_codex_mcp_server(config: dict[str, object]) -> tuple[Path, bool]:
    config_path = _codex_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    server_name = _require_text(config.get("server_name"), "manifest MCP server_name")
    block = [f"[mcp_servers.{server_name}]"]
    block.append(f'command = {_toml_quote(_require_text(config.get("command"), "manifest MCP command"))}')
    block.append(f"args = {_toml_args(_string_list(config.get('args')))}")
    cwd = _as_str(config.get("cwd"))
    if cwd is not None:
        block.append(f"cwd = {_toml_quote(cwd)}")
    env = _string_dict(config.get("env"))
    if env:
        block.append(f"env = {_toml_env(env)}")
    startup_timeout = _number_value(config.get("startup_timeout_sec"))
    if startup_timeout is not None:
        block.append(f"startup_timeout_sec = {startup_timeout}")
    tool_timeout = _number_value(config.get("tool_timeout_sec"))
    if tool_timeout is not None:
        block.append(f"tool_timeout_sec = {tool_timeout}")
    updated = _replace_or_append_toml_section(existing, block[0], block)
    changed = updated != existing
    if changed or not config_path.exists():
        config_path.write_text(updated, encoding="utf-8")
    return config_path, changed


def _install_claude_mcp_server(config: dict[str, object]) -> tuple[Path, bool]:
    config_path = Path.home() / ".claude.json"
    data = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Claude JSON config: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Claude config must be a JSON object")
    current_servers = data.get("mcpServers")
    if current_servers is None:
        current_servers = {}
    if not isinstance(current_servers, dict):
        raise ValueError("Claude config field 'mcpServers' must be an object")
    entry = {
        "type": "stdio",
        "command": _require_text(config.get("command"), "manifest MCP command"),
        "args": _string_list(config.get("args")),
    }
    cwd = _as_str(config.get("cwd"))
    if cwd is not None:
        entry["cwd"] = cwd
    env = _string_dict(config.get("env"))
    if env:
        entry["env"] = env
    server_name = _require_text(config.get("server_name"), "manifest MCP server_name")
    changed = current_servers.get(server_name) != entry
    if changed or not config_path.exists():
        current_servers = dict(current_servers)
        current_servers[server_name] = entry
        data = dict(data)
        data["mcpServers"] = current_servers
        config_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path, changed


def _codex_config_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _replace_or_append_toml_section(text: str, header: str, block: list[str]) -> str:
    lines = text.splitlines()
    output = []
    index = 0
    replaced = False
    while index < len(lines):
        line = lines[index]
        if _is_toml_section_header(line) and line.strip() == header:
            if output and output[-1].strip():
                output.append("")
            output.extend(block)
            output.append("")
            replaced = True
            index += 1
            while index < len(lines) and not _is_toml_section_header(lines[index]):
                index += 1
            continue
        output.append(line)
        index += 1
    if not replaced:
        if output and output[-1].strip():
            output.append("")
        output.extend(block)
        output.append("")
    return "\n".join(output).rstrip() + "\n"


def _is_toml_section_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_args(values: list[str]) -> str:
    return "[" + ", ".join(_toml_quote(value) for value in values) + "]"


def _toml_env(values: dict[str, str]) -> str:
    pairs = [f"{key} = {_toml_quote(values[key])}" for key in sorted(values)]
    return "{ " + ", ".join(pairs) + " }"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _string_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    items = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        text = _as_str(item)
        if text is None:
            continue
        items[key] = text
    return items


def _number_value(value: object) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _require_text(value: object, label: str) -> str:
    text = _as_str(value)
    if text is None:
        raise ValueError(f"{label} is missing")
    return text


def _as_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


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
