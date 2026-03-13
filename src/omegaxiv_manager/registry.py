"""Registry resolution for `ox` installs."""

from __future__ import annotations

import difflib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ResolvedPackage:
    handle: str
    version: str
    distribution_name: str
    install_target: str
    requirements_url: str | None
    dependency_graph_url: str | None
    package_type: str
    summary: str
    record_url: str
    index_url: str


@dataclass(frozen=True)
class SearchResult:
    handle: str
    latest_version: str
    package_type: str
    summary: str


class RegistryClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or _default_base_url()).rstrip("/")

    @property
    def index_url(self) -> str:
        return f"{self._base_url}/packages/index.json"

    def record_url(self, handle: str) -> str:
        return f"{self._base_url}/handles/{handle}.json"

    def resolve(self, handle: str, version: str | None = None) -> ResolvedPackage:
        index = self._read_json(self.index_url)
        handles = index.get("handles")
        if not isinstance(handles, dict):
            raise ValueError("registry index is malformed.")
        resolved = _resolve_handle_entry(handles, handle)
        if resolved is None:
            raise ValueError(_unknown_handle_message(handle, handles, self.index_url))
        resolved_handle, entry = resolved
        record_path = _as_str(entry.get("record_path")) or f"handles/{resolved_handle}.json"
        record_url = f"{self._base_url}/{record_path.lstrip('/')}"
        record = self._read_json(record_url)
        versions = record.get("versions")
        if not isinstance(versions, list) or not versions:
            raise ValueError(f"handle has no releases: {resolved_handle}")
        selected = _select_version(versions, version)
        if selected is None:
            raise ValueError(f"version not found for {resolved_handle}: {version}")
        selected_version = _as_str(selected.get("version"))
        if selected_version is None:
            raise ValueError(f"release metadata is invalid for {resolved_handle}")
        distribution_name = _as_str(selected.get("distribution_name")) or resolved_handle
        install_target = _as_str(selected.get("install_target"))
        if install_target is None:
            install_target = _first_artifact_url(selected) or distribution_name
        repo_url = _as_str(record.get("repo_url")) or _as_str(entry.get("repo_url"))
        repo_raw_base_url = _repo_raw_base_url(repo_url)
        requirements_url = _resolve_release_file_url(
            registry_base_url=self._base_url,
            repo_raw_base_url=repo_raw_base_url,
            release=selected,
            path=_as_str(selected.get("requirements_path")),
        )
        dependency_graph_url = _resolve_release_file_url(
            registry_base_url=self._base_url,
            repo_raw_base_url=repo_raw_base_url,
            release=selected,
            path=_as_str(selected.get("dependency_graph_path")),
        )
        return ResolvedPackage(
            handle=resolved_handle,
            version=selected_version,
            distribution_name=distribution_name,
            install_target=install_target,
            requirements_url=requirements_url,
            dependency_graph_url=dependency_graph_url,
            package_type=_as_str(record.get("package_type")) or "library",
            summary=_as_str(entry.get("summary")) or "",
            record_url=record_url,
            index_url=self.index_url,
        )

    def search(self, query: str) -> list[SearchResult]:
        index = self._read_json(self.index_url)
        handles = index.get("handles")
        if not isinstance(handles, dict):
            return []
        token = query.strip().lower()
        results = []
        for handle, entry in handles.items():
            if not isinstance(handle, str) or not isinstance(entry, dict):
                continue
            summary = _as_str(entry.get("summary")) or ""
            package_type = _as_str(entry.get("package_type")) or "library"
            latest_version = _as_str(entry.get("latest_version")) or "unknown"
            if token and token not in handle.lower() and token not in summary.lower():
                continue
            results.append(
                SearchResult(
                    handle=handle,
                    latest_version=latest_version,
                    package_type=package_type,
                    summary=summary,
                )
            )
        return sorted(results, key=lambda item: item.handle)

    def show(self, handle: str) -> dict[str, object]:
        index = self._read_json(self.index_url)
        handles = index.get("handles")
        if not isinstance(handles, dict):
            raise ValueError("registry index is malformed.")
        resolved = _resolve_handle_entry(handles, handle)
        if resolved is None:
            raise ValueError(_unknown_handle_message(handle, handles, self.index_url))
        resolved_handle, entry = resolved
        record_path = _as_str(entry.get("record_path")) or f"handles/{resolved_handle}.json"
        record_url = f"{self._base_url}/{record_path.lstrip('/')}"
        record = self._read_json(record_url)
        return {
            "handle": resolved_handle,
            "latest_version": _as_str(entry.get("latest_version")),
            "package_type": _as_str(entry.get("package_type"))
            or _as_str(record.get("package_type")),
            "summary": _as_str(entry.get("summary")) or "",
            "record_url": record_url,
            "versions": record.get("versions") if isinstance(record.get("versions"), list) else [],
        }

    def _read_json(self, url: str) -> dict[str, object]:
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ValueError(f"registry request failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            raise ValueError(f"registry request failed: {reason}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"registry response is not an object: {url}")
        return payload


def _select_version(versions: list[object], version: str | None) -> dict[str, object] | None:
    if version is None:
        last = versions[-1]
        return last if isinstance(last, dict) else None
    for item in versions:
        if not isinstance(item, dict):
            continue
        if _as_str(item.get("version")) == version:
            return item
    return None


def _first_artifact_url(release: dict[str, object]) -> str | None:
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        url = _as_str(item.get("url"))
        if url is not None:
            return url
    return None


def _default_base_url() -> str:
    direct = os.environ.get("OX_REGISTRY_BASE_URL", "").strip()
    if direct:
        return direct
    org = os.environ.get("OX_REGISTRY_ORG", "omegaXiv-labs").strip() or "omegaXiv-labs"
    repo = os.environ.get("OX_REGISTRY_REPO", "omegaxiv-registry").strip() or "omegaxiv-registry"
    branch = os.environ.get("OX_REGISTRY_BRANCH", "main").strip() or "main"
    return f"https://raw.githubusercontent.com/{org}/{repo}/{branch}"


def _as_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_registry_url(base_url: str, value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("https://") or value.startswith("http://"):
        return value
    return f"{base_url}/{value.lstrip('/')}"


def _resolve_release_file_url(
    *,
    registry_base_url: str,
    repo_raw_base_url: str | None,
    release: dict[str, object],
    path: str | None,
) -> str | None:
    if path is None:
        return None
    if path.startswith("https://") or path.startswith("http://"):
        return path
    artifact_url = _artifact_url_for_path(release, path)
    if artifact_url is not None:
        return artifact_url
    relative_candidates = _candidate_release_paths(path)
    base_candidates = [value for value in (repo_raw_base_url, registry_base_url) if value is not None]
    for base_url in base_candidates:
        for relative_path in relative_candidates:
            candidate_url = f"{base_url.rstrip('/')}/{relative_path}"
            if _url_exists(candidate_url):
                return candidate_url
    first_base = repo_raw_base_url or registry_base_url
    return f"{first_base.rstrip('/')}/{relative_candidates[0]}"


def _artifact_url_for_path(release: dict[str, object], path: str) -> str | None:
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, list):
        return None
    normalized_path = _normalize_release_path(path)
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        artifact_url = _as_str(item.get("url"))
        if artifact_url is None:
            continue
        artifact_path = _normalize_release_path(_as_str(item.get("path")))
        if artifact_path != normalized_path:
            continue
        return artifact_url
    return None


def _candidate_release_paths(path: str) -> list[str]:
    normalized = _normalize_release_path(path)
    assert normalized is not None
    candidates = []
    if normalized.startswith("workspace/"):
        without_workspace = normalized.removeprefix("workspace/").lstrip("/")
        if without_workspace:
            candidates.append(without_workspace)
    else:
        candidates.append(normalized)
        candidates.append(f"workspace/{normalized}")
    if normalized.startswith("package/"):
        tail = normalized.removeprefix("package/").strip("/")
        if tail:
            candidates.append(f"packages/{tail}")
            candidates.append(f"workspace/packages/{tail}")
    if normalized.startswith("workspace/package/"):
        tail = normalized.removeprefix("workspace/package/").strip("/")
        if tail:
            candidates.append(f"packages/{tail}")
            candidates.append(f"workspace/packages/{tail}")
    if normalized.startswith("packages/"):
        tail = normalized.removeprefix("packages/").strip("/")
        if tail:
            candidates.append(f"workspace/packages/{tail}")
    if normalized.startswith("workspace/packages/"):
        tail = normalized.removeprefix("workspace/packages/").strip("/")
        if tail:
            candidates.append(f"packages/{tail}")
    unique_candidates = []
    seen = set()
    for candidate in candidates:
        compact = candidate.strip().lstrip("/")
        if not compact or compact in seen:
            continue
        seen.add(compact)
        unique_candidates.append(compact)
    return unique_candidates


def _normalize_release_path(path: str | None) -> str | None:
    if path is None:
        return None
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        return None
    return normalized.lstrip("/")


def _url_exists(url: str) -> bool:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=8):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        return False
    except urllib.error.URLError:
        return False


def _repo_raw_base_url(repo_url: str | None) -> str | None:
    if repo_url is None:
        return None
    parsed = urlparse(repo_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner = parts[-2].strip()
    repository = parts[-1].strip().removesuffix(".git")
    if not owner or not repository:
        return None
    return f"https://raw.githubusercontent.com/{owner}/{repository}/main"


def _resolve_handle_entry(
    handles: dict[str, object],
    handle: str,
) -> tuple[str, dict[str, object]] | None:
    entry = handles.get(handle)
    if isinstance(entry, dict):
        return handle, entry
    normalized = _normalize_handle(handle)
    if normalized is None:
        return None
    entry = handles.get(normalized)
    if isinstance(entry, dict):
        return normalized, entry
    for candidate, value in handles.items():
        if not isinstance(candidate, str) or not isinstance(value, dict):
            continue
        canonical = _as_str(value.get("handle"))
        if canonical is not None and canonical == normalized:
            return candidate, value
    return None


def _unknown_handle_message(handle: str, handles: dict[str, object], index_url: str) -> str:
    lookup = _normalize_handle(handle) or handle.strip().lower()
    options = [name for name, value in handles.items() if isinstance(name, str) and isinstance(value, dict)]
    suggestions = difflib.get_close_matches(lookup, sorted(options), n=3, cutoff=0.55)
    suggestion_text = "" if not suggestions else f" Suggested handles: {', '.join(suggestions)}."
    return (
        f"unknown handle: {handle}. Registry index does not contain this handle ({index_url})."
        f"{suggestion_text} If this handle was just packaged, ensure registry sync completed before install."
    )


def _normalize_handle(value: str) -> str | None:
    stripped = value.strip().lower()
    if not stripped:
        return None
    normalized = []
    dash_open = False
    for char in stripped:
        if char.isalnum():
            normalized.append(char)
            dash_open = False
            continue
        if not dash_open:
            normalized.append("-")
            dash_open = True
    collapsed = "".join(normalized).strip("-")
    return collapsed or None
