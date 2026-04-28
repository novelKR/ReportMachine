#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Sequence


IMAGE_BINDER_KO_NOTICE = (
    "이 이미지는 ZIP 파일을 포함한 File Binder 기반 실험용 내부 배포 파일입니다. "
    "이미지 파일만 허용되는 특수한 제한 환경에서 공유하기 위한 옵션입니다."
)
IMAGE_BINDER_EN_NOTICE = (
    "This image contains a ZIP file and uses a file-binder based experimental "
    "internal distribution format. It is intended only for special image-only "
    "transfer constraints."
)
IMAGE_BINDER_ENGLISH_ONLY_NOTICE = (
    "English-only fallback: Korean text rendering was not guaranteed in this "
    "environment, so this cover renders only English warning text."
)
IMAGE_BINDER_ACK_FLAG = "--acknowledge-image-only-internal-distribution"
IMAGE_BINDER_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.otf",
    "C:/Windows/Fonts/malgun.ttf",
]
IMAGE_BINDER_LATIN_FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


class PackageSourceError(RuntimeError):
    pass


def run_command(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def require_success(process: subprocess.CompletedProcess[str], description: str) -> str:
    if process.returncode == 0:
        return process.stdout.strip()
    detail = (process.stderr or process.stdout).strip()
    raise PackageSourceError(f"{description} failed: {detail}")


def find_git_root(cwd: Path) -> Path:
    output = require_success(
        run_command(["git", "rev-parse", "--show-toplevel"], cwd=cwd),
        "git root detection",
    )
    root = Path(output).resolve()
    if not (root / ".article-root").exists() or not (root / "scripts" / "verify_layout.py").exists():
        raise PackageSourceError("current Git repository is not a ReportMachine source tree")
    return root


def head_short_sha(root: Path) -> str:
    return require_success(
        run_command(["git", "rev-parse", "--short", "HEAD"], cwd=root),
        "HEAD revision lookup",
    )


def dirty_status(root: Path) -> str:
    return require_success(
        run_command(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root),
        "working tree status check",
    )


def normalize_prefix(prefix: str) -> str:
    value = prefix.strip().replace("\\", "/")
    value = value.rstrip("/")
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PackageSourceError("--prefix must be a relative archive directory name")
    return path.as_posix() + "/"


def resolve_output_path(
    root: Path,
    output: str | None,
    short_sha: str,
    *,
    image_binder: bool = False,
) -> Path:
    if output:
        path = Path(output)
    elif image_binder:
        path = Path("dist") / "source" / f"ReportMachine-source-{short_sha}-image-binder.jpg"
    else:
        path = Path("dist") / "source" / f"ReportMachine-source-{short_sha}.zip"

    suffix = path.suffix.lower()
    if image_binder:
        if suffix not in {".jpg", ".jpeg"}:
            raise PackageSourceError(
                "--output must end with .jpg or .jpeg when --experimental-image-binder is used"
            )
    elif suffix != ".zip":
        raise PackageSourceError("--output must end with .zip")
    return path if path.is_absolute() else root / path


def display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def archive_root_from_prefix(extract_dir: Path, prefix: str) -> Path:
    return extract_dir.joinpath(*prefix.rstrip("/").split("/"))


def validate_source_archive(archive_path: Path, prefix: str) -> None:
    with tempfile.TemporaryDirectory(prefix="reportmachine-source-verify-") as temp_dir:
        extract_dir = Path(temp_dir)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extract_dir)

        archive_root = archive_root_from_prefix(extract_dir, prefix)
        verify_script = archive_root / "scripts" / "verify_layout.py"
        if not verify_script.exists():
            raise PackageSourceError("archive validation failed: scripts/verify_layout.py is missing")

        process = run_command([sys.executable, "scripts/verify_layout.py", "--release"], cwd=archive_root)
        if process.returncode != 0:
            output = (process.stdout + process.stderr).strip()
            raise PackageSourceError(f"archive validation failed:\n{output}")


def find_image_binder_font_path() -> Path | None:
    for candidate in IMAGE_BINDER_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def find_image_binder_latin_font_path() -> Path | None:
    for candidate in IMAGE_BINDER_LATIN_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def require_pillow_image_modules():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise PackageSourceError(
            "Pillow>=10.1.0 is required for --experimental-image-binder"
        ) from exc
    return Image, ImageDraw, ImageFont


def wrap_notice_text(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = word if not current else current + " " + word
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = word
        else:
            lines.append(word)

    if current:
        lines.append(current)
    return lines


def create_image_binder_cover(cover_path: Path) -> None:
    Image, ImageDraw, ImageFont = require_pillow_image_modules()
    font_path = find_image_binder_font_path()
    render_korean = font_path is not None
    if font_path is None:
        font_path = find_image_binder_latin_font_path()

    width = 1400
    height = 900
    margin = 96
    image = Image.new("RGB", (width, height), (247, 248, 250))
    draw = ImageDraw.Draw(image)
    if font_path is not None:
        title_font = ImageFont.truetype(str(font_path), 52)
        label_font = ImageFont.truetype(str(font_path), 30)
        body_font = ImageFont.truetype(str(font_path), 36)
        footer_font = ImageFont.truetype(str(font_path), 24)
    else:
        title_font = ImageFont.load_default(size=52)
        label_font = ImageFont.load_default(size=30)
        body_font = ImageFont.load_default(size=36)
        footer_font = ImageFont.load_default(size=24)

    draw.rectangle((0, 0, width, 28), fill=(176, 42, 55))
    draw.text((margin, 92), "ReportMachine Experimental Image Binder", font=title_font, fill=(20, 28, 40))

    y = 178
    if render_korean:
        draw.text((margin, y), "Korean notice", font=label_font, fill=(176, 42, 55))
        y += 48
        for line in wrap_notice_text(draw, IMAGE_BINDER_KO_NOTICE, body_font, width - margin * 2):
            draw.text((margin, y), line, font=body_font, fill=(20, 28, 40))
            y += 54
        y += 36
    else:
        draw.text((margin, y), "English-only fallback", font=label_font, fill=(176, 42, 55))
        y += 48
        for line in wrap_notice_text(
            draw,
            IMAGE_BINDER_ENGLISH_ONLY_NOTICE,
            body_font,
            width - margin * 2,
        ):
            draw.text((margin, y), line, font=body_font, fill=(20, 28, 40))
            y += 54
        y += 36

    draw.text((margin, y), "English notice", font=label_font, fill=(176, 42, 55))
    y += 48
    for line in wrap_notice_text(draw, IMAGE_BINDER_EN_NOTICE, body_font, width - margin * 2):
        draw.text((margin, y), line, font=body_font, fill=(20, 28, 40))
        y += 54

    draw.line((margin, height - 116, width - margin, height - 116), fill=(205, 211, 220), width=2)
    draw.text(
        (margin, height - 82),
        "Internal image-only transfer constraint option. The appended payload remains a validated ZIP archive.",
        font=footer_font,
        fill=(67, 78, 92),
    )
    image.save(cover_path, format="JPEG", quality=92, optimize=False, progressive=False)


def create_image_binder_archive(source_zip: Path, output_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="reportmachine-image-binder-") as temp_dir:
        cover_path = Path(temp_dir) / "image-binder-cover.jpg"
        create_image_binder_cover(cover_path)
        with output_path.open("wb") as output:
            output.write(cover_path.read_bytes())
            output.write(source_zip.read_bytes())


def build_archive(root: Path, output_path: Path, prefix: str, *, image_binder: bool = False) -> None:
    with tempfile.TemporaryDirectory(prefix="reportmachine-source-") as temp_dir:
        temp_zip = Path(temp_dir) / "ReportMachine-source.zip"
        require_success(
            run_command(
                [
                    "git",
                    "archive",
                    "--format=zip",
                    f"--prefix={prefix}",
                    "--output",
                    str(temp_zip),
                    "HEAD",
                ],
                cwd=root,
            ),
            "git archive",
        )
        validate_source_archive(temp_zip, prefix)
        temp_output = temp_zip
        if image_binder:
            temp_output = Path(temp_dir) / output_path.name
            create_image_binder_archive(temp_zip, temp_output)
            validate_source_archive(temp_output, prefix)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        shutil.move(str(temp_output), output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a validated Git HEAD source zip for ReportMachine distribution."
    )
    parser.add_argument("--output", help="Output zip path. Defaults to dist/source/ReportMachine-source-{short_sha}.zip.")
    parser.add_argument("--prefix", help="Archive root directory prefix. Defaults to ReportMachine-source-{short_sha}/.")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow packaging from a dirty working tree. The archive still uses Git HEAD only.",
    )
    parser.add_argument(
        "--experimental-image-binder",
        action="store_true",
        help=(
            "Create an experimental internal-only JPG image binder with a validated ZIP "
            "payload appended after a visible warning cover."
        ),
    )
    parser.add_argument(
        IMAGE_BINDER_ACK_FLAG,
        action="store_true",
        help=(
            "Acknowledge that the experimental image binder is only for internal "
            "image-only transfer constraints that preserve original JPG bytes."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print packaging details without creating a zip.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        root = find_git_root(Path.cwd())
        if args.acknowledge_image_only_internal_distribution and not args.experimental_image_binder:
            raise PackageSourceError(
                f"{IMAGE_BINDER_ACK_FLAG} requires --experimental-image-binder"
            )
        if args.experimental_image_binder and not args.acknowledge_image_only_internal_distribution:
            raise PackageSourceError(
                f"--experimental-image-binder requires {IMAGE_BINDER_ACK_FLAG}"
            )

        status = dirty_status(root)
        if status and not args.allow_dirty:
            raise PackageSourceError(
                "working tree has uncommitted changes; commit/stash them or pass --allow-dirty "
                "to package Git HEAD only\n" + status
            )

        short_sha = head_short_sha(root)
        prefix = normalize_prefix(args.prefix or f"ReportMachine-source-{short_sha}")
        output_path = resolve_output_path(
            root,
            args.output,
            short_sha,
            image_binder=args.experimental_image_binder,
        )

        print(f"repo_root: {root}")
        print(f"head: {short_sha}")
        print(f"prefix: {prefix}")
        print(f"output: {display_path(root, output_path)}")
        if args.experimental_image_binder:
            print("experimental_image_binder: enabled; visible JPG cover with appended validated ZIP payload")
        if status and args.allow_dirty:
            print("allow_dirty: packaging Git HEAD only; uncommitted changes are excluded")

        if args.dry_run:
            print("dry_run: no archive created")
            return 0

        build_archive(root, output_path, prefix, image_binder=args.experimental_image_binder)
        print(f"created: {display_path(root, output_path)}")
        return 0
    except PackageSourceError as exc:
        print(f"package-source failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
