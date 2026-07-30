"""ReportLab A4 typesetting for ReportMachine articles.

Font policy
-----------
Hangul text must be drawn with an embedded TrueType face. Two alternatives were
measured and rejected:

* ReportLab's built-in CJK ``UnicodeCIDFont`` faces (for example
  ``HYSMyeongJo-Medium``) reference the ``Adobe-Korea1`` CMap collection. Poppler
  based viewers report ``Missing language pack for 'Adobe-Korea1' mapping`` and
  render the affected text runs as a *blank* page, so the article silently
  disappears. Never register a CID font here.
* The Type 1 base fonts (``Helvetica`` and friends) have no Hangul coverage and
  render every syllable as ``.notdef`` boxes.

Only ``TTFont`` registration is used. Bold is optional because portable system
locations do not reliably ship a bold Hangul TrueType face (macOS provides
``AppleGothic.ttf`` regular only). When no bold face resolves, the bold slot is
mapped onto the regular file and heading hierarchy relies on size and colour.
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .config import PublishProfile
from .publish_markdown import (
    BlockQuote,
    CodeBlock,
    Document,
    Heading,
    ImageBlock,
    ListBlock,
    Paragraph as MdParagraph,
    Table as MdTable,
    ThematicBreak,
    strip_inline_markers,
)


FONT_REGULAR_NAME = "RMBody"
FONT_BOLD_NAME = "RMBodyBold"
FONT_MONO_NAME = "Courier"

# Candidate regular Hangul TrueType faces per platform, most portable first.
REGULAR_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "darwin": (
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/Supplemental/AppleMyungjo.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ),
    "linux": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansKR-Regular.otf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
    "win32": (
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/gulim.ttc",
        "C:/Windows/Fonts/batang.ttc",
    ),
}

BOLD_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "darwin": (
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    ),
    "linux": (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    ),
    "win32": (
        "C:/Windows/Fonts/malgunbd.ttf",
    ),
}

FONT_INSTALL_HINT = (
    "no Hangul TrueType font found for PDF publishing. "
    "Set profiles.<name>.publish.font_regular to a .ttf/.ttc path, or install a "
    "Korean font such as Noto Sans CJK KR or NanumGothic."
)


class PublishFontError(RuntimeError):
    """Raised when no usable Hangul TrueType face can be registered."""


@dataclass(frozen=True)
class ResolvedFonts:
    regular_path: str
    bold_path: str | None

    def as_manifest(self) -> dict[str, str | None]:
        return {"regular": self.regular_path, "bold": self.bold_path}


@dataclass
class RenderResult:
    page_count: int
    figure_count: int
    warnings: list[str]
    fonts: ResolvedFonts


@dataclass(frozen=True)
class FigureSpec:
    """A resolved article image ready for placement."""

    number: int
    absolute_path: Path
    caption: str


def _platform_key() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform in {"win32", "cygwin"}:
        return "win32"
    return "linux"


def _try_register(name: str, path: Path) -> bool:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont, TTFError

    for subfont_index in (0, 1):
        try:
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=subfont_index))
            return True
        except TTFError:
            continue
        except Exception:
            continue
    return False


def resolve_fonts(profile: PublishProfile, root: Path) -> ResolvedFonts:
    """Register the body fonts, returning the paths actually used.

    Raises PublishFontError when no regular Hangul face can be registered.
    """

    from reportlab.pdfbase import pdfmetrics

    platform_key = _platform_key()

    regular_candidates: list[str] = []
    if profile.font_regular:
        regular_candidates.append(profile.font_regular)
    regular_candidates.extend(REGULAR_FONT_CANDIDATES.get(platform_key, ()))

    regular_path: str | None = None
    for candidate in regular_candidates:
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        if _try_register(FONT_REGULAR_NAME, path):
            regular_path = str(path)
            break

    if regular_path is None:
        configured = f" configured font_regular={profile.font_regular!r} was unusable;" if profile.font_regular else ""
        raise PublishFontError(f"{FONT_INSTALL_HINT}{configured}")

    bold_candidates: list[str] = []
    if profile.font_bold:
        bold_candidates.append(profile.font_bold)
    bold_candidates.extend(BOLD_FONT_CANDIDATES.get(platform_key, ()))

    bold_path: str | None = None
    for candidate in bold_candidates:
        path = Path(candidate).expanduser()
        if not path.is_file():
            continue
        if str(path) == regular_path:
            # Same file as regular: no real bold face, use the fallback mapping.
            continue
        if _try_register(FONT_BOLD_NAME, path):
            bold_path = str(path)
            break

    if bold_path is None:
        # Bold is optional. Map the bold slot onto the regular face so inline
        # <b> markup and family lookups stay valid without a bold TTF.
        pdfmetrics.registerFontFamily(
            FONT_REGULAR_NAME,
            normal=FONT_REGULAR_NAME,
            bold=FONT_REGULAR_NAME,
            italic=FONT_REGULAR_NAME,
            boldItalic=FONT_REGULAR_NAME,
        )
    else:
        pdfmetrics.registerFontFamily(
            FONT_REGULAR_NAME,
            normal=FONT_REGULAR_NAME,
            bold=FONT_BOLD_NAME,
            italic=FONT_REGULAR_NAME,
            boldItalic=FONT_BOLD_NAME,
        )

    return ResolvedFonts(regular_path=regular_path, bold_path=bold_path)


def _heading_font(fonts: ResolvedFonts) -> str:
    return FONT_BOLD_NAME if fonts.bold_path else FONT_REGULAR_NAME


def _escape(text: str) -> str:
    """Escape for ReportLab's mini-HTML paragraph parser."""

    return html.escape(text, quote=False)


_STRONG_PATTERN = re.compile(r"\*\*(.+?)\*\*")
_EMPHASIS_PATTERN = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_CODE_PATTERN = re.compile(r"`([^`]+)`")
_LINK_PATTERN = re.compile(r"\[(?P<label>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_IMAGE_PATTERN = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")


def render_inline(
    text: str,
    *,
    link_numbers: dict[str, int] | None = None,
    link_color: str = "#1a4d8f",
) -> str:
    """Convert inline Markdown to ReportLab paragraph markup.

    Escaping happens first so article text containing `&`, `<`, or `>` cannot
    inject markup into the paragraph parser.
    """

    working = _IMAGE_PATTERN.sub(lambda m: m.group("alt"), text)
    working = _escape(working)

    def link_replacement(match: re.Match[str]) -> str:
        label = match.group("label")
        target = html.unescape(match.group("target"))
        rendered_label = _inline_styles(label)
        suffix = ""
        if link_numbers is not None:
            number = link_numbers.get(target)
            if number is not None:
                suffix = f"<super>[{number}]</super>"
        safe_target = target.replace('"', "%22")
        return (
            f'<a href="{safe_target}" color="{link_color}">{rendered_label}</a>{suffix}'
        )

    working = _LINK_PATTERN.sub(link_replacement, working)
    return _inline_styles(working)


def _inline_styles(text: str) -> str:
    working = _STRONG_PATTERN.sub(r"<b>\1</b>", text)
    working = _EMPHASIS_PATTERN.sub(r"<i>\1</i>", working)
    working = _CODE_PATTERN.sub(
        lambda m: f'<font face="{FONT_MONO_NAME}">{m.group(1)}</font>', working
    )
    return working


def _build_styles(profile: PublishProfile, fonts: ResolvedFonts):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle

    heading_font = _heading_font(fonts)
    body_size = profile.body_font_size
    leading = profile.body_leading
    heading_color = colors.HexColor(profile.heading_color)
    muted = colors.HexColor(profile.caption_color)

    return {
        "title": ParagraphStyle(
            "RMTitle",
            fontName=heading_font,
            fontSize=body_size + 11.5,
            leading=body_size + 18,
            textColor=heading_color,
            spaceAfter=10,
        ),
        "subtitle": ParagraphStyle(
            "RMSubtitle",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size + 1.5,
            leading=body_size + 9,
            textColor=muted,
            spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "RMH1",
            fontName=heading_font,
            fontSize=body_size + 6.5,
            leading=body_size + 13,
            textColor=heading_color,
            spaceBefore=18,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "RMH2",
            fontName=heading_font,
            fontSize=body_size + 3,
            leading=body_size + 10,
            textColor=heading_color,
            spaceBefore=16,
            spaceAfter=7,
        ),
        "h3": ParagraphStyle(
            "RMH3",
            fontName=heading_font,
            fontSize=body_size + 1,
            leading=body_size + 8,
            textColor=heading_color,
            spaceBefore=13,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "RMBodyText",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size,
            leading=leading,
            spaceAfter=7,
        ),
        "caption": ParagraphStyle(
            "RMCaption",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size - 2,
            leading=body_size + 3,
            textColor=muted,
            spaceBefore=5,
            spaceAfter=12,
        ),
        "quote": ParagraphStyle(
            "RMQuote",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size,
            leading=leading,
            leftIndent=14,
            textColor=muted,
            spaceAfter=8,
        ),
        "code": ParagraphStyle(
            "RMCode",
            fontName=FONT_MONO_NAME,
            fontSize=body_size - 1.5,
            leading=body_size + 3,
            leftIndent=10,
        ),
        "toc": ParagraphStyle(
            "RMToc",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size,
            leading=leading - 1,
        ),
        "toc_sub": ParagraphStyle(
            "RMTocSub",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size - 1,
            leading=leading - 3,
            leftIndent=14,
            textColor=muted,
        ),
        "footnote": ParagraphStyle(
            "RMFootnote",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size - 2.5,
            leading=body_size + 2,
            leftIndent=16,
            firstLineIndent=-16,
            spaceAfter=3,
        ),
        "meta": ParagraphStyle(
            "RMMeta",
            fontName=FONT_REGULAR_NAME,
            fontSize=body_size - 1,
            leading=body_size + 5,
            textColor=muted,
        ),
    }


def _image_flowable(spec: FigureSpec, *, max_width: float, max_height: float):
    from reportlab.platypus import Image

    width, height = _image_size(spec.absolute_path)
    if width <= 0 or height <= 0:
        return Image(str(spec.absolute_path), width=max_width, height=max_width * 0.6)
    scale = min(max_width / width, max_height / height)
    return Image(str(spec.absolute_path), width=width * scale, height=height * scale)


def _image_size(path: Path) -> tuple[float, float]:
    from reportlab.lib.utils import ImageReader

    try:
        return ImageReader(str(path)).getSize()
    except Exception:
        return (0.0, 0.0)


def build_story(
    document: Document,
    *,
    profile: PublishProfile,
    fonts: ResolvedFonts,
    figures: dict[str, FigureSpec],
    link_numbers: dict[str, int],
    link_footnotes: Sequence[tuple[int, str, str]],
    metadata: dict[str, str],
    frame_width: float,
    frame_height: float,
) -> list:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable,
        KeepTogether,
        PageBreak,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    styles = _build_styles(profile, fonts)
    story: list = []

    headings = [block for block in document.blocks if isinstance(block, Heading)]
    title_block = next((block for block in headings if block.level == 1), None)
    title_text = metadata.get("title") or (title_block.text if title_block else "")

    if profile.include_cover:
        story.append(Spacer(1, 42 * mm))
        story.append(Paragraph(render_inline(title_text), styles["title"]))
        if metadata.get("subtitle"):
            story.append(Paragraph(render_inline(metadata["subtitle"]), styles["subtitle"]))
        story.append(HRFlowable(width="100%", color=colors.HexColor(profile.rule_color)))
        story.append(Spacer(1, 8 * mm))
        if metadata.get("one_sentence_insight"):
            story.append(
                Paragraph(render_inline(metadata["one_sentence_insight"]), styles["body"])
            )
        story.append(Spacer(1, 6 * mm))
        for label in ("run_id", "article_type", "article_sha256"):
            if metadata.get(label):
                story.append(
                    Paragraph(f"{label}: {_escape(metadata[label])}", styles["meta"])
                )
        story.append(PageBreak())

    if profile.include_toc:
        toc_entries = [block for block in headings if block.level in {2, 3}]
        if toc_entries:
            story.append(Paragraph("목차", styles["h1"]))
            for entry in toc_entries:
                style = styles["toc"] if entry.level == 2 else styles["toc_sub"]
                story.append(Paragraph(render_inline(entry.text), style))
            story.append(PageBreak())

    body_max_height = frame_height * profile.max_image_height_ratio
    skip_first_h1 = profile.include_cover and title_block is not None

    for block in document.blocks:
        if isinstance(block, Heading):
            if skip_first_h1 and block is title_block:
                continue
            style = styles[f"h{block.level}"]
            story.append(Paragraph(render_inline(block.text, link_numbers=link_numbers), style))
            continue

        if isinstance(block, MdParagraph):
            story.append(
                Paragraph(render_inline(block.text, link_numbers=link_numbers), styles["body"])
            )
            continue

        if isinstance(block, ListBlock):
            for item in block.items:
                bullet = f"{item.number}." if item.ordered and item.number else "•"
                indent = 12 + item.depth * 14
                style = styles["body"].clone(
                    f"RMListItem{item.depth}",
                    leftIndent=indent,
                    firstLineIndent=-10,
                    spaceAfter=3,
                )
                rendered = render_inline(item.text, link_numbers=link_numbers)
                story.append(Paragraph(f"{bullet} {rendered}", style))
            story.append(Spacer(1, 4))
            continue

        if isinstance(block, BlockQuote):
            for line in block.lines:
                story.append(
                    Paragraph(render_inline(line, link_numbers=link_numbers), styles["quote"])
                )
            continue

        if isinstance(block, CodeBlock):
            for line in block.lines:
                story.append(Paragraph(_escape(line) or "&nbsp;", styles["code"]))
            story.append(Spacer(1, 6))
            continue

        if isinstance(block, MdTable):
            data = [
                [
                    Paragraph(render_inline(cell, link_numbers=link_numbers), styles["body"])
                    for cell in block.header
                ]
            ]
            for row in block.rows:
                data.append(
                    [
                        Paragraph(render_inline(cell, link_numbers=link_numbers), styles["body"])
                        for cell in row
                    ]
                )
            table = Table(data, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(profile.rule_color)),
                        (
                            "BACKGROUND",
                            (0, 0),
                            (-1, 0),
                            colors.HexColor(profile.table_header_color),
                        ),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 8))
            continue

        if isinstance(block, ImageBlock):
            spec = figures.get(block.target)
            if spec is None:
                continue
            flowable = _image_flowable(
                spec, max_width=frame_width, max_height=body_max_height
            )
            caption = Paragraph(
                f"그림 {spec.number}. {render_inline(spec.caption)}", styles["caption"]
            )
            # Keep the figure and its caption on one page so a caption never
            # appears detached from the evidence it describes.
            story.append(KeepTogether([flowable, caption]))
            continue

        if isinstance(block, ThematicBreak):
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", color=colors.HexColor(profile.rule_color)))
            story.append(Spacer(1, 6))
            continue

    if profile.include_link_footnotes and link_footnotes:
        story.append(PageBreak())
        story.append(Paragraph("링크 각주", styles["h1"]))
        story.append(
            Paragraph(
                "본문 인용 링크의 전체 주소입니다. 인쇄본에서도 출처를 확인할 수 있습니다.",
                styles["meta"],
            )
        )
        story.append(Spacer(1, 6))
        for number, label, url in link_footnotes:
            text = f"[{number}] {_escape(label)}<br/>{_escape(url)}" if label else f"[{number}] {_escape(url)}"
            story.append(Paragraph(text, styles["footnote"]))

    return story


def render_pdf(
    document: Document,
    *,
    output_path: Path,
    profile: PublishProfile,
    fonts: ResolvedFonts,
    figures: dict[str, FigureSpec],
    link_numbers: dict[str, int],
    link_footnotes: Sequence[tuple[int, str, str]],
    metadata: dict[str, str],
) -> int:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    page_size = landscape(A4) if profile.orientation == "landscape" else A4
    margin = profile.margin_mm * mm
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=page_size,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=metadata.get("title", ""),
        author=metadata.get("run_id", ""),
        subject=metadata.get("subtitle", ""),
        # invariant=1 pins the PDF CreationDate and document /ID so identical
        # inputs render byte-identical output.
        invariant=1,
    )

    footer_color = colors.HexColor(profile.caption_color)
    footer_label = metadata.get("run_id", "")

    def draw_footer(canvas, current_doc) -> None:
        canvas.saveState()
        canvas.setFont(FONT_REGULAR_NAME, max(7.0, profile.body_font_size - 3))
        canvas.setFillColor(footer_color)
        canvas.drawCentredString(
            page_size[0] / 2.0, margin / 2.2, str(canvas.getPageNumber())
        )
        if footer_label:
            canvas.drawRightString(page_size[0] - margin, margin / 2.2, footer_label)
        canvas.restoreState()

    story = build_story(
        document,
        profile=profile,
        fonts=fonts,
        figures=figures,
        link_numbers=link_numbers,
        link_footnotes=link_footnotes,
        metadata=metadata,
        frame_width=doc.width,
        frame_height=doc.height,
    )
    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return doc.page


def plain_text(text: str) -> str:
    return strip_inline_markers(text)
