from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from omegaxiv_manager import cli
from omegaxiv_manager.registry import RegistryClient, ResolvedPackage


def test_cl_act_requirements_resolve_to_package_repo_url() -> None:
    client = RegistryClient("https://raw.githubusercontent.com/omegaXiv-labs/omegaxiv-registry/main")
    index_payload = {
        "handles": {
            "cl-act": {
                "handle": "cl-act",
                "latest_version": "0.1.3",
                "record_path": "handles/cl-act.json",
                "repo_url": "https://github.com/omegaXiv-labs/omegaXiv-run-bc36d98c-6b8e-435c-8783-221723ad93ff",
            }
        }
    }
    record_payload = {
        "handle": "cl-act",
        "package_type": "library",
        "repo_url": "https://github.com/omegaXiv-labs/omegaXiv-run-bc36d98c-6b8e-435c-8783-221723ad93ff",
        "versions": [
            {
                "version": "0.1.3",
                "distribution_name": "activation-cl-validation",
                "install_target": "activation-cl-validation",
                "requirements_path": "packages/cl-act/requirements.txt",
                "dependency_graph_path": "packages/cl-act/dependency_graph.json",
                "artifacts": [],
            }
        ],
    }
    expected_base = (
        "https://raw.githubusercontent.com/"
        "omegaXiv-labs/omegaXiv-run-bc36d98c-6b8e-435c-8783-221723ad93ff/main"
    )
    with (
        patch.object(RegistryClient, "_read_json", side_effect=[index_payload, record_payload]),
        patch("omegaxiv_manager.registry._url_exists", return_value=True),
    ):
        resolved = client.resolve("cl-act")
    assert resolved.handle == "cl-act"
    assert resolved.requirements_url == f"{expected_base}/packages/cl-act/requirements.txt"
    assert resolved.dependency_graph_url == f"{expected_base}/packages/cl-act/dependency_graph.json"


def test_ox_install_cl_act_passes_resolved_requirements_url_to_pip() -> None:
    resolved = ResolvedPackage(
        handle="cl-act",
        version="0.1.3",
        distribution_name="activation-cl-validation",
        install_target="activation-cl-validation",
        requirements_url=(
            "https://raw.githubusercontent.com/"
            "omegaXiv-labs/omegaXiv-run-bc36d98c-6b8e-435c-8783-221723ad93ff/main/"
            "packages/cl-act/requirements.txt"
        ),
        dependency_graph_url=None,
        package_type="library",
        summary="",
        record_url="https://raw.githubusercontent.com/omegaXiv-labs/omegaxiv-registry/main/handles/cl-act.json",
        index_url="https://raw.githubusercontent.com/omegaXiv-labs/omegaxiv-registry/main/packages/index.json",
    )

    class _FakeRegistry:
        def resolve(self, handle: str, version: str | None) -> ResolvedPackage:
            assert handle == "cl-act"
            assert version is None
            return resolved

    class _FakeState:
        def __init__(self) -> None:
            self.saved = None

        def get(self, _handle: str):
            return None

        def upsert(self, **kwargs) -> None:
            self.saved = kwargs

    args = SimpleNamespace(
        command="install",
        spec="cl-act",
        isolated=False,
        global_install=False,
        python_executable=None,
    )
    state = _FakeState()
    with (
        patch(
            "omegaxiv_manager.cli._resolve_install_context",
            return_value=(None, None, ["python", "-m", "pip"]),
        ),
        patch("omegaxiv_manager.cli._pip_install") as pip_install,
        patch("builtins.print"),
    ):
        cli._run_command(args, _FakeRegistry(), state)
    pip_install.assert_called_once_with(
        ["python", "-m", "pip"],
        "activation-cl-validation",
        upgrade=False,
        requirements_url=resolved.requirements_url,
    )
    assert state.saved is not None
    assert state.saved["handle"] == "cl-act"
    assert state.saved["version"] == "0.1.3"
