# omegaxiv (`ox`)

Python-first package manager client for omegaXiv registry handles.

## Install locally

```bash
pip install .
```

## Commands

```bash
ox install <handle>[==version]
ox install <handle>[==version] --mcp
ox install <handle>[==version] --mcp=codex
ox install <handle>[==version] --mcp=claude
ox install <handle>[==version] --mcp=all
ox upgrade <handle>
ox uninstall <handle>
ox list
ox search <query>
ox show <handle>
```

Install mode defaults:

- `library` packages install into the current Python environment.
- other package types install into a managed venv at `~/.omegaxiv/envs/<handle>`.

Overrides:

```bash
ox install <handle> --isolated
ox install <handle> --global-install
ox install <handle> --isolated --python python3.11
```

`ox install` resolves and installs package dependency requirements from registry metadata before installing the target distribution.

When `--mcp` is present, `ox install` also performs best-effort local MCP registration for Codex
and/or Claude using the packaged `packaging_manifest.json`. If the requested package version is
already installed locally, `ox install <handle> --mcp...` reuses the existing package install and
only updates the local MCP client config.

## Registry configuration

- `OX_REGISTRY_BASE_URL` (optional full base URL)
- `OX_REGISTRY_ORG` (default: `alphaomega-labs`)
- `OX_REGISTRY_REPO` (default: `registry`)
- `OX_REGISTRY_BRANCH` (default: `main`)
- `OX_STATE_PATH` (optional local state path)
- `OX_PYTHON_BIN` (default Python executable for isolated venv creation)
