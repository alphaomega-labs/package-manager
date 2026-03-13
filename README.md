# omegaxiv (`ox`)

Python-first package manager client for omegaXiv registry handles.

## Install locally

```bash
pip install .
```

## Commands

```bash
ox install <handle>[==version]
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

## Registry configuration

- `OX_REGISTRY_BASE_URL` (optional full base URL)
- `OX_REGISTRY_ORG` (default: `omegaXiv-labs`)
- `OX_REGISTRY_REPO` (default: `omegaxiv-registry`)
- `OX_REGISTRY_BRANCH` (default: `main`)
- `OX_STATE_PATH` (optional local state path)
- `OX_PYTHON_BIN` (default Python executable for isolated venv creation)
