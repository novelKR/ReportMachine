from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from .source_fetch import _hash_file, _relative_to_root


def _pdf_page_count(pdf_path: Path) -> int:
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        result = subprocess.run(
            [pdfinfo, str(pdf_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.lower().startswith("pages:"):
                    _, value = line.split(":", 1)
                    try:
                        return max(0, int(value.strip()))
                    except ValueError:
                        return 0
    try:
        data = pdf_path.read_bytes()
    except OSError:
        return 0
    return len(re.findall(rb"/Type\s*/Page\b", data))


def _text_excerpt(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:500]


def _term_frequency(text: str) -> dict[str, int]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.%-]*", text.lower())
    counts = Counter(token for token in tokens if len(token) > 1)
    return dict(counts.most_common(200))


def _extract_pdf_page_text(pdf_path: Path, page: int, text_path: Path) -> tuple[str, str]:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        return "tool_missing", ""
    text_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            pdftotext,
            "-layout",
            "-enc",
            "UTF-8",
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(text_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "failed", result.stderr.strip()[:500]
    return "ok", text_path.read_text(encoding="utf-8", errors="replace")


def _render_pdf_page(
    *,
    root: Path,
    pdf_path: Path,
    source_id: str,
    page: int,
    output_dir: Path,
    dpi: int,
) -> tuple[str, str, str, str]:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        return "", "", "tool_missing", "pdftoppm not found"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{source_id}_page_{page:03d}.png"
    prefix = output_dir / f".{source_id}_page_{page:03d}"
    for stale in output_dir.glob(f"{prefix.name}*.png"):
        stale.unlink()
    result = subprocess.run(
        [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            str(prefix),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return "", "", "failed", result.stderr.strip()[:500]
    generated = sorted(output_dir.glob(f"{prefix.name}*.png"))
    if not generated:
        return "", "", "failed", "pdftoppm produced no image"
    if target.exists():
        target.unlink()
    generated[0].replace(target)
    for extra in generated[1:]:
        extra.unlink()
    return _relative_to_root(root, target), _hash_file(target), "ok", ""
