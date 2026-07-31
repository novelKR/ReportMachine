"""Orchestration for the deterministic A4 publish output.

This workflow performs no Codex calls, no network access, and no external API
requests. It reads completed article artifacts and renders a PDF locally, so
the same inputs always produce the same document.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .config import PublishProfile, RunnerProfile, load_profile_config, resolve_config_path
from .execution_control import run_with_execution_control
from .paths import as_posix_arg, find_project_root, resolve_under_root, validate_run_id
from .publish_contracts import (
    PUBLISH_SCHEMA,
    PublishPaths,
    PublishRunConfig,
    PublishSourcePaths,
    _read_json,
    _repo_path,
    build_publish_paths,
    build_publish_source_paths,
    collect_input_errors,
    ensure_publish_directory,
    output_files,
    publish_dependency_error,
    source_hashes,
    validate_source_hashes,
    write_failure_publish_result,
    write_figure_map,
    write_publish_manifest,
    write_publish_result,
)
from .publish_markdown import collect_links, parse_markdown
from .schema_validation import schema_validation_dependency_error, validate_json_artifact


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            {key: (value or "") for key, value in row.items() if key is not None}
            for row in reader
        ]


def _normalize_target(target: str) -> str:
    """Normalize an article image reference without rewriting its meaning.

    Only a leading `./` is removed. Parent-directory and absolute markers are
    preserved so `resolve_under_root` can reject paths that escape the project
    root instead of them being silently flattened into repo-local paths.
    """

    normalized = target.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def build_publish_run_config(
    *,
    root: Path,
    run_id: str,
    schema: str = PUBLISH_SCHEMA,
    profile: PublishProfile | None = None,
    sources: PublishSourcePaths | None = None,
) -> PublishRunConfig:
    run_id = validate_run_id(run_id, "--run-id")
    schema_rel = as_posix_arg(resolve_under_root(root, schema).relative_to(root.resolve()))
    return PublishRunConfig(
        root=root,
        run_id=run_id,
        schema=schema_rel,
        paths=build_publish_paths(run_id),
        sources=sources or build_publish_source_paths(run_id),
        profile=profile or PublishProfile(),
    )


def mirror_latest_publish(root: Path, paths: PublishPaths) -> None:
    source_dir = resolve_under_root(root, paths.publish_dir)
    if not source_dir.is_dir():
        return
    latest_publish = resolve_under_root(root, "outputs/latest/publish")
    temp_dir = latest_publish.with_name(f".{latest_publish.name}.tmp")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    shutil.copytree(source_dir, temp_dir)
    if latest_publish.exists():
        shutil.rmtree(latest_publish)
    temp_dir.replace(latest_publish)


def resolve_figures(
    config: PublishRunConfig,
    image_targets: Sequence[str],
) -> tuple[dict[str, object], list[dict[str, object]], list[str], list[str]]:
    """Map article image references to ledger-backed figure specs.

    Returns (figures, figure_map_rows, warnings, errors). Captions come from the
    image ledger so the PDF never introduces text absent from the ledgers.
    """

    from .publish_pdf import FigureSpec

    ledger_rows = _read_csv_rows(_repo_path(config.root, config.sources.image_ledger))
    ledger_by_path: dict[str, dict[str, str]] = {}
    for row in ledger_rows:
        key = _normalize_target(row.get("image_path", "").strip())
        if key:
            ledger_by_path.setdefault(key, row)

    figures: dict[str, object] = {}
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    errors: list[str] = []

    number = 0
    for order, target in enumerate(image_targets, start=1):
        normalized = _normalize_target(target)
        try:
            absolute = resolve_under_root(config.root, normalized)
        except ValueError as exc:
            errors.append(f"article image path is not repo-local: {target} ({exc})")
            continue
        if not absolute.is_file():
            errors.append(f"article references a missing image file: {normalized}")
            continue

        ledger = ledger_by_path.get(normalized, {})
        keep_or_drop = ledger.get("keep_or_drop", "").strip().lower()
        if keep_or_drop in {"drop", "dropped", "false", "no"}:
            warnings.append(
                "article references an image marked keep_or_drop=drop in the image ledger: "
                f"{normalized}"
            )

        caption = ledger.get("caption", "").strip()
        caption_source = "image_ledger.caption"
        if not caption:
            caption = ledger.get("caption_candidate", "").strip()
            caption_source = "image_ledger.caption_candidate"
        if not caption:
            caption = ""
            caption_source = "article_alt_text"
        if not ledger:
            warnings.append(f"article image is not listed in the image ledger: {normalized}")

        number += 1
        source_id = ledger.get("source_id", "").strip()
        page = ledger.get("page_or_slide_number", "").strip()
        attribution_parts = [part for part in [source_id, f"p.{page}" if page else ""] if part]
        display_caption = caption
        if attribution_parts:
            display_caption = (
                f"{caption} · 출처: {' '.join(attribution_parts)}"
                if caption
                else f"출처: {' '.join(attribution_parts)}"
            )

        figures[target] = FigureSpec(
            number=number,
            absolute_path=absolute,
            caption=display_caption,
        )
        rows.append(
            {
                "figure_number": number,
                "image_path": normalized,
                "source_id": source_id,
                "page_or_slide_number": page,
                "caption": display_caption,
                "caption_source": caption_source,
                "keep_or_drop": ledger.get("keep_or_drop", "").strip(),
                "article_reference_order": order,
                "status": "rendered",
                "notes": "",
            }
        )

    return figures, rows, warnings, errors


def _article_metadata(
    config: PublishRunConfig,
    article_result: dict[str, object],
    *,
    source_digest: str = "",
) -> dict[str, str]:
    # The cover records the article digest rather than a wall-clock timestamp so
    # identical inputs render byte-identical PDFs. The rendering time is still
    # recorded in publish_manifest.json.
    return {
        "title": str(article_result.get("title", "") or ""),
        "subtitle": str(article_result.get("subtitle", "") or ""),
        "one_sentence_insight": str(article_result.get("one_sentence_insight", "") or ""),
        "article_type": str(article_result.get("article_type", "") or ""),
        "run_id": config.run_id,
        "article_sha256": source_digest[:16],
    }


def validate_publish_result(
    config: PublishRunConfig,
    *,
    expected_hashes: dict[str, str] | None = None,
) -> list[str]:
    payload, errors = validate_json_artifact(
        root=config.root,
        artifact=config.paths.publish_result,
        schema=config.schema,
        label="publish result",
    )
    if payload is None:
        return errors
    if not isinstance(payload, dict):
        return errors + ["publish result root must be an object"]

    if payload.get("source_article") != config.sources.article:
        errors.append("publish result source_article does not match configured article path")
    if payload.get("source_result") != config.sources.article_result:
        errors.append("publish result source_result does not match configured article result path")
    if expected_hashes is not None:
        if payload.get("source_article_sha256") != expected_hashes.get("article_sha256"):
            errors.append("publish result source_article_sha256 does not match article hash")
        if payload.get("source_result_sha256") != expected_hashes.get("article_result_sha256"):
            errors.append("publish result source_result_sha256 does not match article result hash")

    actual_outputs = payload.get("output_files")
    if not isinstance(actual_outputs, dict):
        errors.append("publish result output_files must be an object")
    else:
        for key, expected in output_files(config).items():
            if actual_outputs.get(key) != expected:
                errors.append(f"publish result output_files.{key} must be {expected}")

    if payload.get("checklist_status") in {"pass", "pass_with_warnings"}:
        for label, value in [
            ("article_pdf", config.paths.article_pdf),
            ("publish_figure_map", config.paths.publish_figure_map),
            ("publish_manifest", config.paths.publish_manifest),
        ]:
            if not _repo_path(config.root, value).is_file():
                errors.append(f"missing {label}: {value}")
    return errors


def run_publish_workflow(
    config: PublishRunConfig,
    *,
    dry_run: bool = False,
    smoke_test: bool = False,
    mirror_latest: bool = True,
) -> int:
    input_errors = collect_input_errors(config)
    if input_errors:
        for error in input_errors:
            print(error, file=sys.stderr)
        return 2

    article_result = _read_json(_repo_path(config.root, config.sources.article_result))
    article_status = str(article_result.get("checklist_status", ""))

    if dry_run or smoke_test:
        print("cwd:", config.root)
        print("run_id:", config.run_id)
        print("publish_dir:", config.paths.publish_dir)
        print("article_pdf:", config.paths.article_pdf)
        print("publish_result:", config.paths.publish_result)
        print("article_status:", article_status)
        print("render_surface: local_deterministic_renderer")
        if smoke_test:
            print("publish smoke-test: inputs resolved without rendering")
        return 0

    dependency_error = publish_dependency_error()
    if dependency_error:
        print(dependency_error, file=sys.stderr)
        return 2
    # publish_result.json is schema-validated at the end of this workflow, so the
    # schema dependency is probed up front rather than failing after rendering.
    schema_dependency_error = schema_validation_dependency_error()
    if schema_dependency_error:
        print(schema_dependency_error, file=sys.stderr)
        return 2

    hashes = source_hashes(config)
    ensure_publish_directory(config)

    if article_status == "fail":
        warning = "source article checklist_status is fail; PDF publishing skipped"
        write_figure_map(config, [])
        write_publish_manifest(
            config,
            article_status=article_status,
            hashes=hashes,
            fonts={"regular": "", "bold": None},
            page_count=0,
            figure_count=0,
            warnings=[warning],
        )
        write_failure_publish_result(config, warnings=[warning], hashes=hashes)
        print(warning, file=sys.stderr)
        return 2

    from .publish_pdf import PublishFontError, render_pdf, resolve_fonts

    article_text = _repo_path(config.root, config.sources.article).read_text(encoding="utf-8")
    document = parse_markdown(article_text)
    warnings: list[str] = list(document.warnings)

    figures, figure_rows, figure_warnings, figure_errors = resolve_figures(
        config, document.image_targets()
    )
    warnings.extend(figure_warnings)

    if figure_errors:
        write_figure_map(config, figure_rows)
        write_failure_publish_result(config, warnings=figure_errors + warnings, hashes=hashes)
        print("Publish figure resolution failed:", file=sys.stderr)
        for error in figure_errors:
            print(f" - {error}", file=sys.stderr)
        return 2

    try:
        fonts = resolve_fonts(config.profile, config.root)
    except PublishFontError as exc:
        message = str(exc)
        write_figure_map(config, figure_rows)
        write_failure_publish_result(config, warnings=[message] + warnings, hashes=hashes)
        print(message, file=sys.stderr)
        return 2

    if fonts.bold_path is None:
        warnings.append(
            "no bold Hangul font resolved; heading hierarchy uses size and colour contrast"
        )

    links = collect_links(document.blocks)
    link_numbers = {url: index for index, (_, url) in enumerate(links, start=1)}
    link_footnotes = [(link_numbers[url], label, url) for label, url in links]

    metadata = _article_metadata(
        config, article_result, source_digest=hashes.get("article_sha256", "")
    )

    try:
        page_count = render_pdf(
            document,
            output_path=_repo_path(config.root, config.paths.article_pdf),
            profile=config.profile,
            fonts=fonts,
            figures=figures,  # type: ignore[arg-type]
            link_numbers=link_numbers,
            link_footnotes=link_footnotes,
            metadata=metadata,
        )
    except Exception as exc:  # pragma: no cover - defensive rendering guard
        message = f"PDF rendering failed: {type(exc).__name__}: {exc}"
        write_figure_map(config, figure_rows)
        write_failure_publish_result(
            config, warnings=[message] + warnings, hashes=hashes, fonts=fonts.as_manifest()
        )
        print(message, file=sys.stderr)
        return 2

    hash_errors = validate_source_hashes(config, hashes)
    if hash_errors:
        write_figure_map(config, figure_rows)
        write_failure_publish_result(
            config, warnings=hash_errors + warnings, hashes=hashes, fonts=fonts.as_manifest()
        )
        print("Publish guard failed:", file=sys.stderr)
        for error in hash_errors:
            print(f" - {error}", file=sys.stderr)
        return 2

    figure_count = len(figure_rows)
    status = "pass_with_warnings" if warnings else "pass"
    write_figure_map(config, figure_rows)
    write_publish_manifest(
        config,
        article_status=article_status,
        hashes=hashes,
        fonts=fonts.as_manifest(),
        page_count=page_count,
        figure_count=figure_count,
        warnings=warnings,
    )
    write_publish_result(
        config,
        status=status,
        hashes=hashes,
        fonts=fonts.as_manifest(),
        page_count=page_count,
        figure_count=figure_count,
        warnings=warnings,
    )

    validation_errors = validate_publish_result(config, expected_hashes=hashes)
    if validation_errors:
        write_failure_publish_result(
            config,
            warnings=validation_errors + warnings,
            hashes=hashes,
            fonts=fonts.as_manifest(),
        )
        print("Publish validation failed:", file=sys.stderr)
        for error in validation_errors:
            print(f" - {error}", file=sys.stderr)
        return 2

    if mirror_latest:
        mirror_latest_publish(config.root, config.paths)

    print("publish_pdf:", config.paths.article_pdf)
    print("publish_pages:", page_count)
    print("publish_figures:", figure_count)
    print("publish_result:", config.paths.publish_result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a completed ReportMachine article to an A4 PDF."
    )
    parser.add_argument("--run-id", required=True, help="Run id under outputs/{run_id}.")
    parser.add_argument("--project-root", help="ReportMachine project root.")
    parser.add_argument("--config", help="TOML runner preset file.")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--schema", default=PUBLISH_SCHEMA)
    parser.add_argument("--font-regular", help="Override the regular Hangul TrueType font path.")
    parser.add_argument("--font-bold", help="Override the bold Hangul TrueType font path.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument(
        "--no-mirror-latest",
        action="store_true",
        help="Do not refresh outputs/latest/publish after a successful render.",
    )
    return parser


def _load_profile(root: Path, config_value: str | None, profile_name: str) -> RunnerProfile:
    if not config_value:
        return RunnerProfile()
    return load_profile_config(resolve_config_path(root, config_value), profile_name)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = (
        Path(args.project_root).expanduser().resolve()
        if args.project_root
        else find_project_root()
    )
    try:
        loaded_profile = _load_profile(root, args.config, args.profile)
        publish_profile = loaded_profile.publish
        if args.font_regular or args.font_bold:
            from dataclasses import replace

            publish_profile = replace(
                publish_profile,
                font_regular=args.font_regular or publish_profile.font_regular,
                font_bold=args.font_bold or publish_profile.font_bold,
            )
        config = build_publish_run_config(
            root=root,
            run_id=args.run_id,
            schema=args.schema,
            profile=publish_profile,
        )
        if args.dry_run or args.smoke_test:
            return run_publish_workflow(
                config,
                dry_run=args.dry_run,
                smoke_test=args.smoke_test,
                mirror_latest=not args.no_mirror_latest,
            )
        return run_with_execution_control(
            root,
            lambda: run_publish_workflow(
                config,
                mirror_latest=not args.no_mirror_latest,
            ),
            operation="publish",
            run_id=args.run_id,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return 2
