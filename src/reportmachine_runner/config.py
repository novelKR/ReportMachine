from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_KEYS = {
    "mode",
    "topic",
    "sources",
    "schema",
    "web_search",
    "allow_shell_network",
    "run_id_prefix",
    "artifacts",
}
ARTIFACT_KEYS = {
    "external_root",
    "import_existing",
    "snapshot_interval_seconds",
    "cleanup_cache_on_success",
    "preserve_cache_on_failure",
}


@dataclass(frozen=True)
class ArtifactProfile:
    external_root: str | None = None
    import_existing: bool = False
    snapshot_interval_seconds: int = 0
    cleanup_cache_on_success: bool = False
    preserve_cache_on_failure: bool = True


@dataclass(frozen=True)
class RunnerProfile:
    mode: str | None = None
    topic: str | None = None
    sources: str | None = None
    schema: str | None = None
    web_search: str | None = None
    allow_shell_network: bool | None = None
    run_id_prefix: str | None = None
    artifacts: ArtifactProfile = ArtifactProfile()


def resolve_config_path(root: Path, value: str) -> Path:
    if not value:
        raise ValueError("Config path must not be empty")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a TOML table")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_bool(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _bool(value: Any, label: str, default: bool) -> bool:
    parsed = _optional_bool(value, label)
    return default if parsed is None else parsed


def _interval(value: Any, label: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _validate_keys(table: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"{label} has unknown key(s): {joined}")


def _load_toml(handle: Any) -> dict[str, Any]:
    try:  # pragma: no cover - Python 3.11+ path
        import tomllib
    except ModuleNotFoundError:
        try:
            import tomli as tomllib
        except ModuleNotFoundError as exc:
            raise ValueError(
                "TOML config support requires Python 3.11+ or the tomli package on Python 3.10"
            ) from exc
    return tomllib.load(handle)


def load_profile_config(config_path: Path, profile_name: str) -> RunnerProfile:
    if not profile_name:
        raise ValueError("Profile name must not be empty")
    if not config_path.is_file():
        raise ValueError(f"Config file does not exist: {config_path}")

    with config_path.open("rb") as handle:
        data = _load_toml(handle)

    profiles = _require_mapping(data.get("profiles"), "profiles")
    profile = _require_mapping(profiles.get(profile_name), f"profiles.{profile_name}")
    _validate_keys(profile, PROFILE_KEYS, f"profiles.{profile_name}")

    artifacts_table = _require_mapping(
        profile.get("artifacts", {}),
        f"profiles.{profile_name}.artifacts",
    )
    _validate_keys(
        artifacts_table,
        ARTIFACT_KEYS,
        f"profiles.{profile_name}.artifacts",
    )

    artifacts = ArtifactProfile(
        external_root=_optional_string(
            artifacts_table.get("external_root"),
            f"profiles.{profile_name}.artifacts.external_root",
        ),
        import_existing=_bool(
            artifacts_table.get("import_existing"),
            f"profiles.{profile_name}.artifacts.import_existing",
            False,
        ),
        snapshot_interval_seconds=_interval(
            artifacts_table.get("snapshot_interval_seconds"),
            f"profiles.{profile_name}.artifacts.snapshot_interval_seconds",
        ),
        cleanup_cache_on_success=_bool(
            artifacts_table.get("cleanup_cache_on_success"),
            f"profiles.{profile_name}.artifacts.cleanup_cache_on_success",
            False,
        ),
        preserve_cache_on_failure=_bool(
            artifacts_table.get("preserve_cache_on_failure"),
            f"profiles.{profile_name}.artifacts.preserve_cache_on_failure",
            True,
        ),
    )

    return RunnerProfile(
        mode=_optional_string(profile.get("mode"), f"profiles.{profile_name}.mode"),
        topic=_optional_string(profile.get("topic"), f"profiles.{profile_name}.topic"),
        sources=_optional_string(profile.get("sources"), f"profiles.{profile_name}.sources"),
        schema=_optional_string(profile.get("schema"), f"profiles.{profile_name}.schema"),
        web_search=_optional_string(
            profile.get("web_search"),
            f"profiles.{profile_name}.web_search",
        ),
        allow_shell_network=_optional_bool(
            profile.get("allow_shell_network"),
            f"profiles.{profile_name}.allow_shell_network",
        ),
        run_id_prefix=_optional_string(
            profile.get("run_id_prefix"),
            f"profiles.{profile_name}.run_id_prefix",
        ),
        artifacts=artifacts,
    )
