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
    "publish",
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
PUBLISH_KEYS = {
    "enabled",
    "paper_size",
    "orientation",
    "margin_mm",
    "body_font_size",
    "body_leading",
    "font_regular",
    "font_bold",
    "heading_color",
    "caption_color",
    "rule_color",
    "table_header_color",
    "include_cover",
    "include_toc",
    "include_link_footnotes",
    "max_image_height_ratio",
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
class PublishProfile:
    enabled: bool = False
    paper_size: str = "A4"
    orientation: str = "portrait"
    margin_mm: float = 20.0
    body_font_size: float = 10.5
    body_leading: float = 17.5
    font_regular: str | None = None
    font_bold: str | None = None
    heading_color: str = "#12263a"
    caption_color: str = "#5a6672"
    rule_color: str = "#c8d0d8"
    table_header_color: str = "#eef2f6"
    include_cover: bool = True
    include_toc: bool = True
    include_link_footnotes: bool = True
    max_image_height_ratio: float = 0.6


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
    publish: PublishProfile = PublishProfile()
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


def _positive_float(value: Any, label: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a positive number")
    if value <= 0:
        raise ValueError(f"{label} must be a positive number")
    return float(value)


def _ratio(value: Any, label: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number greater than 0 and at most 1")
    if not 0 < value <= 1:
        raise ValueError(f"{label} must be a number greater than 0 and at most 1")
    return float(value)


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
    publish_table = _require_mapping(
        profile.get("publish", {}),
        f"profiles.{profile_name}.publish",
    )
    _validate_keys(
        publish_table,
        PUBLISH_KEYS,
        f"profiles.{profile_name}.publish",
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

    publish_defaults = PublishProfile()
    publish = PublishProfile(
        enabled=_bool(
            publish_table.get("enabled"),
            f"profiles.{profile_name}.publish.enabled",
            publish_defaults.enabled,
        ),
        paper_size=_string(
            publish_table.get("paper_size"),
            f"profiles.{profile_name}.publish.paper_size",
            publish_defaults.paper_size,
        ),
        orientation=_string(
            publish_table.get("orientation"),
            f"profiles.{profile_name}.publish.orientation",
            publish_defaults.orientation,
        ),
        margin_mm=_positive_float(
            publish_table.get("margin_mm"),
            f"profiles.{profile_name}.publish.margin_mm",
            publish_defaults.margin_mm,
        ),
        body_font_size=_positive_float(
            publish_table.get("body_font_size"),
            f"profiles.{profile_name}.publish.body_font_size",
            publish_defaults.body_font_size,
        ),
        body_leading=_positive_float(
            publish_table.get("body_leading"),
            f"profiles.{profile_name}.publish.body_leading",
            publish_defaults.body_leading,
        ),
        font_regular=_optional_string(
            publish_table.get("font_regular"),
            f"profiles.{profile_name}.publish.font_regular",
        ),
        font_bold=_optional_string(
            publish_table.get("font_bold"),
            f"profiles.{profile_name}.publish.font_bold",
        ),
        heading_color=_string(
            publish_table.get("heading_color"),
            f"profiles.{profile_name}.publish.heading_color",
            publish_defaults.heading_color,
        ),
        caption_color=_string(
            publish_table.get("caption_color"),
            f"profiles.{profile_name}.publish.caption_color",
            publish_defaults.caption_color,
        ),
        rule_color=_string(
            publish_table.get("rule_color"),
            f"profiles.{profile_name}.publish.rule_color",
            publish_defaults.rule_color,
        ),
        table_header_color=_string(
            publish_table.get("table_header_color"),
            f"profiles.{profile_name}.publish.table_header_color",
            publish_defaults.table_header_color,
        ),
        include_cover=_bool(
            publish_table.get("include_cover"),
            f"profiles.{profile_name}.publish.include_cover",
            publish_defaults.include_cover,
        ),
        include_toc=_bool(
            publish_table.get("include_toc"),
            f"profiles.{profile_name}.publish.include_toc",
            publish_defaults.include_toc,
        ),
        include_link_footnotes=_bool(
            publish_table.get("include_link_footnotes"),
            f"profiles.{profile_name}.publish.include_link_footnotes",
            publish_defaults.include_link_footnotes,
        ),
        max_image_height_ratio=_ratio(
            publish_table.get("max_image_height_ratio"),
            f"profiles.{profile_name}.publish.max_image_height_ratio",
            publish_defaults.max_image_height_ratio,
        ),
    )
    if publish.orientation not in {"portrait", "landscape"}:
        raise ValueError(
            f"profiles.{profile_name}.publish.orientation must be 'portrait' or 'landscape'"
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
        publish=publish,
        run_id_prefix=_optional_string(
            profile.get("run_id_prefix"),
            f"profiles.{profile_name}.run_id_prefix",
        ),
        artifacts=artifacts,
    )
