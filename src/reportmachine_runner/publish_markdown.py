"""Deterministic Markdown reader for the A4 publish renderer.

This module converts `article.md` into a small document model. It is
intentionally dependency-free so the parsing contract can be tested in
environments that do not have the optional PDF rendering dependency
installed.

Only the Markdown subset that ReportMachine articles actually use is
supported. Anything outside that subset is preserved as plain text and
reported as a warning instead of being silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence


HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
UNORDERED_ITEM_PATTERN = re.compile(r"^(\s*)[-*+]\s+(.*)$")
ORDERED_ITEM_PATTERN = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
BLOCK_IMAGE_PATTERN = re.compile(
    r"^!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$"
)
INLINE_IMAGE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)
LINK_PATTERN = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE_PATTERN = re.compile(r"^\s*(```+|~~~+)\s*(?P<info>\S*)\s*$")
THEMATIC_BREAK_PATTERN = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
BLOCKQUOTE_PATTERN = re.compile(r"^\s*>\s?(.*)$")
TABLE_DELIMITER_PATTERN = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
HTML_BLOCK_PATTERN = re.compile(r"^\s*<[a-zA-Z/!][^>]*>")

# Markdown constructs that this renderer does not lay out specially. They are
# rendered as plain text, and each distinct kind is reported once.
UNSUPPORTED_MARKERS = (
    ("footnote_definition", re.compile(r"^\s*\[\^[^\]]+\]:")),
    ("html_block", HTML_BLOCK_PATTERN),
    ("definition_list", re.compile(r"^\s*:\s{2,}\S")),
)

MAX_LIST_DEPTH = 2


@dataclass(frozen=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True)
class Paragraph:
    text: str


@dataclass(frozen=True)
class ListItem:
    text: str
    depth: int
    ordered: bool
    number: int | None = None


@dataclass(frozen=True)
class ListBlock:
    items: tuple[ListItem, ...]


@dataclass(frozen=True)
class BlockQuote:
    lines: tuple[str, ...]


@dataclass(frozen=True)
class CodeBlock:
    lines: tuple[str, ...]
    language: str = ""


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class ImageBlock:
    alt: str
    target: str


@dataclass(frozen=True)
class ThematicBreak:
    pass


Block = (
    Heading
    | Paragraph
    | ListBlock
    | BlockQuote
    | CodeBlock
    | Table
    | ImageBlock
    | ThematicBreak
)


@dataclass
class Document:
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def image_targets(self) -> list[str]:
        return [block.target for block in self.blocks if isinstance(block, ImageBlock)]


def _split_table_row(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return tuple(cell.strip() for cell in stripped.split("|"))


def _is_table_row(line: str) -> bool:
    return "|" in line and line.strip().startswith("|")


def _list_depth(indent: str) -> int:
    # Two spaces per level, matching the article formatting floor. Deeper
    # nesting is clamped and reported so content is never dropped.
    return len(indent.replace("\t", "  ")) // 2


def parse_markdown(text: str) -> Document:
    document = Document()
    reported_warnings: set[str] = set()

    def warn(message: str) -> None:
        if message not in reported_warnings:
            reported_warnings.add(message)
            document.warnings.append(message)

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    total = len(lines)

    paragraph_buffer: list[str] = []
    list_buffer: list[ListItem] = []
    quote_buffer: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_buffer:
            document.blocks.append(Paragraph(" ".join(paragraph_buffer).strip()))
            paragraph_buffer.clear()

    def flush_list() -> None:
        if list_buffer:
            document.blocks.append(ListBlock(tuple(list_buffer)))
            list_buffer.clear()

    def flush_quote() -> None:
        if quote_buffer:
            document.blocks.append(BlockQuote(tuple(quote_buffer)))
            quote_buffer.clear()

    def flush_all() -> None:
        flush_paragraph()
        flush_list()
        flush_quote()

    while index < total:
        line = lines[index]

        if not line.strip():
            flush_all()
            index += 1
            continue

        fence_match = FENCE_PATTERN.match(line)
        if fence_match:
            flush_all()
            fence = fence_match.group(1)[0] * 3
            language = fence_match.group("info")
            index += 1
            code_lines: list[str] = []
            closed = False
            while index < total:
                candidate = lines[index]
                if candidate.strip().startswith(fence):
                    closed = True
                    index += 1
                    break
                code_lines.append(candidate)
                index += 1
            if not closed:
                warn("unclosed fenced code block; rendered to end of document")
            document.blocks.append(CodeBlock(tuple(code_lines), language))
            continue

        heading_match = HEADING_PATTERN.match(line)
        if heading_match:
            flush_all()
            level = len(heading_match.group(1))
            if level > 3:
                warn(f"heading level h{level} rendered at h3 depth")
                level = 3
            document.blocks.append(Heading(level, heading_match.group(2).strip()))
            index += 1
            continue

        if THEMATIC_BREAK_PATTERN.match(line):
            flush_all()
            document.blocks.append(ThematicBreak())
            index += 1
            continue

        block_image = BLOCK_IMAGE_PATTERN.match(line.strip())
        if block_image:
            flush_all()
            document.blocks.append(
                ImageBlock(block_image.group("alt").strip(), block_image.group("target").strip())
            )
            index += 1
            continue

        quote_match = BLOCKQUOTE_PATTERN.match(line)
        if quote_match:
            flush_paragraph()
            flush_list()
            quote_buffer.append(quote_match.group(1).strip())
            index += 1
            continue

        if (
            _is_table_row(line)
            and index + 1 < total
            and TABLE_DELIMITER_PATTERN.match(lines[index + 1])
        ):
            flush_all()
            header = _split_table_row(line)
            index += 2
            rows: list[tuple[str, ...]] = []
            while index < total and _is_table_row(lines[index]):
                rows.append(_split_table_row(lines[index]))
                index += 1
            document.blocks.append(Table(header, tuple(rows)))
            continue

        unordered = UNORDERED_ITEM_PATTERN.match(line)
        ordered = ORDERED_ITEM_PATTERN.match(line)
        if unordered or ordered:
            flush_paragraph()
            flush_quote()
            if unordered:
                indent, content, number = unordered.group(1), unordered.group(2), None
                is_ordered = False
            else:
                assert ordered is not None
                indent, number_text, content = ordered.group(1), ordered.group(2), ordered.group(3)
                number = int(number_text)
                is_ordered = True
            depth = _list_depth(indent)
            if depth > MAX_LIST_DEPTH:
                warn(
                    f"list nesting deeper than {MAX_LIST_DEPTH} levels rendered at depth "
                    f"{MAX_LIST_DEPTH}"
                )
                depth = MAX_LIST_DEPTH
            list_buffer.append(ListItem(content.strip(), depth, is_ordered, number))
            index += 1
            continue

        for label, pattern in UNSUPPORTED_MARKERS:
            if pattern.match(line):
                warn(f"unsupported markdown construct rendered as plain text: {label}")
                break

        flush_list()
        flush_quote()
        paragraph_buffer.append(line.strip())
        index += 1

    flush_all()
    return document


def iter_inline_images(text: str) -> Iterable[re.Match[str]]:
    return INLINE_IMAGE_PATTERN.finditer(text)


def collect_links(blocks: Sequence[Block]) -> list[tuple[str, str]]:
    """Return (label, url) pairs in first-appearance order, deduplicated by url."""

    seen: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []
    for block in blocks:
        for text in _block_texts(block):
            for match in LINK_PATTERN.finditer(text):
                target = match.group("target").strip()
                if not target or target in seen:
                    continue
                label = strip_inline_markers(match.group("label").strip())
                seen[target] = label
                ordered.append((label, target))
    return ordered


def _block_texts(block: Block) -> list[str]:
    if isinstance(block, (Heading, Paragraph)):
        return [block.text]
    if isinstance(block, ListBlock):
        return [item.text for item in block.items]
    if isinstance(block, BlockQuote):
        return list(block.lines)
    if isinstance(block, Table):
        texts = list(block.header)
        for row in block.rows:
            texts.extend(row)
        return texts
    return []


def strip_inline_markers(text: str) -> str:
    without_images = INLINE_IMAGE_PATTERN.sub("", text)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", without_images)
    cleaned = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    return cleaned.strip()
