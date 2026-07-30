"""Deterministic source-text extraction for quote verification.

The extractor intentionally ignores executable and visually hidden HTML content. Source text is
untrusted evidence: scripts, styles, comments, metadata, and embedded prompts must not become quote
material merely because they were present in the downloaded bytes.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from .paths import resolve_under_root
from .source_fetch import _relative_to_root


HIDDEN_TAGS = {
    "canvas",
    "head",
    "iframe",
    "noscript",
    "object",
    "script",
    "style",
    "svg",
    "template",
}
BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
TEXT_SUFFIXES = {".csv", ".json", ".md", ".text", ".txt", ".xml"}


class _VisibleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []
        self._parts: list[str] = []

    def _parent_hidden(self) -> bool:
        return any(hidden for _tag, hidden in self._stack)

    @staticmethod
    def _element_hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        values = {key.lower(): (value or "") for key, value in attrs}
        style = re.sub(r"\s+", "", values.get("style", "").lower())
        classes = set(values.get("class", "").lower().split())
        return (
            tag in HIDDEN_TAGS
            or "hidden" in values
            or values.get("aria-hidden", "").strip().lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
            or "hidden" in classes
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        hidden = self._parent_hidden() or self._element_hidden(normalized, attrs)
        self._stack.append((normalized, hidden))
        if not hidden and normalized in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        hidden = self._parent_hidden() or self._element_hidden(normalized, attrs)
        if not hidden and normalized in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        was_visible = not self._parent_hidden()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == normalized:
                del self._stack[index:]
                break
        if was_visible and normalized in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._parent_hidden():
            self._parts.append(data)

    def text(self) -> str:
        return _normalize_text("".join(self._parts))


def _normalize_text(value: str) -> str:
    value = value.replace("\x00", "")
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.splitlines()]
    compact: list[str] = []
    for line in lines:
        if not line:
            if compact and compact[-1] != "":
                compact.append("")
            continue
        compact.append(line)
    return "\n".join(compact).strip()


def extract_non_pdf_text(path: Path, mime_type: str) -> tuple[str, str]:
    """Return extraction status and visible text for HTML or ordinary text files."""

    mime = mime_type.lower().split(";", 1)[0].strip()
    suffix = path.suffix.lower()
    is_html = mime in {"text/html", "application/xhtml+xml"} or suffix in {".html", ".htm"}
    is_text = mime.startswith("text/") or mime in {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
    } or suffix in TEXT_SUFFIXES
    if not is_html and not is_text:
        return "unsupported", ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return "failed", str(exc)
    if is_html:
        parser = _VisibleHTMLParser()
        try:
            parser.feed(raw)
            parser.close()
        except Exception as exc:  # HTMLParser tolerates malformed markup; retain a clear failure.
            return "failed", str(exc)
        text = parser.text()
    else:
        text = _normalize_text(raw)
    return ("ok", text) if text else ("empty", "")


def write_extracted_text(
    *,
    root: Path,
    extracted_text_dir: str,
    source_id: str,
    text: str,
) -> str:
    target_dir = resolve_under_root(root, extracted_text_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source_id}.txt"
    target.write_text(_normalize_text(text) + "\n", encoding="utf-8")
    return _relative_to_root(root, target)
