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
    "source_ingest",
    "summary",
    "run_id_prefix",
    "artifacts",
}
SOURCE_INGEST_KEYS = {
    "enabled",
    "allowed_domains",
    "max_download_bytes",
    "pdf_render_dpi",
    "max_pdf_pages",
    "max_image_inputs",
}
ARTIFACT_KEYS = {
    "external_root",
    "import_existing",
    "snapshot_interval_seconds",
    "cleanup_cache_on_success",
    "preserve_cache_on_failure",
}
SUMMARY_KEYS = {
    "enabled",
    "image_generation_surface",
    "paper_size",
    "orientation",
    "language",
    "style_name",
    "style_prompt",
    "negative_prompt",
    "max_visual_claims",
    "require_source_footer",
}

SUMMARY_IMAGE_GENERATION_SURFACE = "codex_internal_tool"


@dataclass(frozen=True)
class ArtifactProfile:
    external_root: str | None = None
    import_existing: bool = False
    snapshot_interval_seconds: int = 0
    cleanup_cache_on_success: bool = False
    preserve_cache_on_failure: bool = True


@dataclass(frozen=True)
class SourceIngestProfile:
    enabled: bool | None = None
    allowed_domains: tuple[str, ...] = ()
    max_download_bytes: int | None = None
    pdf_render_dpi: int | None = None
    max_pdf_pages: int | None = None
    max_image_inputs: int | None = None


@dataclass(frozen=True)
class SummaryProfile:
    enabled: bool = False
    image_generation_surface: str = SUMMARY_IMAGE_GENERATION_SURFACE
    paper_size: str = "A4"
    orientation: str = "portrait"
    language: str = "ko"
    style_name: str = "clean_editorial_korean_report"
    style_prompt: str = "Clean editorial A4 Korean industry report summary image."
    negative_prompt: str = "No investment recommendations, price forecasts, unreadable tiny text, or decorative logo collages."
    max_visual_claims: int = 6
    require_source_footer: bool = True


@dataclass(frozen=True)
class RunnerProfile:
    mode: str | None = None
    topic: str | None = None
    sources: str | None = None
    schema: str | None = None
    web_search: str | None = None
    allow_shell_network: bool | None = None
    source_ingest: SourceIngestProfile = SourceIngestProfile()
    summary: SummaryProfile = SummaryProfile()
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


def _optional_non_negative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _string(value: Any, label: str, default: str) -> str:
    parsed = _optional_string(value, label)
    return default if parsed is None else parsed


def _non_negative_int(value: Any, label: str, default: int) -> int:
    parsed = _optional_non_negative_int(value, label)
    return default if parsed is None else parsed


def _optional_string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list of non-empty strings")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label}[{index}] must be a non-empty string")
        values.append(item)
    return tuple(values)


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
    source_ingest_table = _require_mapping(
        profile.get("source_ingest", {}),
        f"profiles.{profile_name}.source_ingest",
    )
    _validate_keys(
        source_ingest_table,
        SOURCE_INGEST_KEYS,
        f"profiles.{profile_name}.source_ingest",
    )
    summary_table = _require_mapping(
        profile.get("summary", {}),
        f"profiles.{profile_name}.summary",
    )
    _validate_keys(
        summary_table,
        SUMMARY_KEYS,
        f"profiles.{profile_name}.summary",
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
    source_ingest = SourceIngestProfile(
        enabled=_optional_bool(
            source_ingest_table.get("enabled"),
            f"profiles.{profile_name}.source_ingest.enabled",
        ),
        allowed_domains=_optional_string_tuple(
            source_ingest_table.get("allowed_domains"),
            f"profiles.{profile_name}.source_ingest.allowed_domains",
        ),
        max_download_bytes=_optional_non_negative_int(
            source_ingest_table.get("max_download_bytes"),
            f"profiles.{profile_name}.source_ingest.max_download_bytes",
        ),
        pdf_render_dpi=_optional_non_negative_int(
            source_ingest_table.get("pdf_render_dpi"),
            f"profiles.{profile_name}.source_ingest.pdf_render_dpi",
        ),
        max_pdf_pages=_optional_non_negative_int(
            source_ingest_table.get("max_pdf_pages"),
            f"profiles.{profile_name}.source_ingest.max_pdf_pages",
        ),
        max_image_inputs=_optional_non_negative_int(
            source_ingest_table.get("max_image_inputs"),
            f"profiles.{profile_name}.source_ingest.max_image_inputs",
        ),
    )
    summary = SummaryProfile(
        enabled=_bool(
            summary_table.get("enabled"),
            f"profiles.{profile_name}.summary.enabled",
            False,
        ),
        image_generation_surface=_string(
            summary_table.get("image_generation_surface"),
            f"profiles.{profile_name}.summary.image_generation_surface",
            SUMMARY_IMAGE_GENERATION_SURFACE,
        ),
        paper_size=_string(
            summary_table.get("paper_size"),
            f"profiles.{profile_name}.summary.paper_size",
            "A4",
        ),
        orientation=_string(
            summary_table.get("orientation"),
            f"profiles.{profile_name}.summary.orientation",
            "portrait",
        ),
        language=_string(
            summary_table.get("language"),
            f"profiles.{profile_name}.summary.language",
            "ko",
        ),
        style_name=_string(
            summary_table.get("style_name"),
            f"profiles.{profile_name}.summary.style_name",
            "clean_editorial_korean_report",
        ),
        style_prompt=_string(
            summary_table.get("style_prompt"),
            f"profiles.{profile_name}.summary.style_prompt",
            "Clean editorial A4 Korean industry report summary image.",
        ),
        negative_prompt=_string(
            summary_table.get("negative_prompt"),
            f"profiles.{profile_name}.summary.negative_prompt",
            "No investment recommendations, price forecasts, unreadable tiny text, or decorative logo collages.",
        ),
        max_visual_claims=_non_negative_int(
            summary_table.get("max_visual_claims"),
            f"profiles.{profile_name}.summary.max_visual_claims",
            6,
        ),
        require_source_footer=_bool(
            summary_table.get("require_source_footer"),
            f"profiles.{profile_name}.summary.require_source_footer",
            True,
        ),
    )
    if summary.image_generation_surface != SUMMARY_IMAGE_GENERATION_SURFACE:
        raise ValueError(
            "profiles."
            f"{profile_name}.summary.image_generation_surface must be {SUMMARY_IMAGE_GENERATION_SURFACE!r}"
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
        source_ingest=source_ingest,
        summary=summary,
        run_id_prefix=_optional_string(
            profile.get("run_id_prefix"),
            f"profiles.{profile_name}.run_id_prefix",
        ),
        artifacts=artifacts,
    )
