from __future__ import annotations

import csv
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.config import PublishProfile
from reportmachine_runner.publish import (
    PUBLISH_FIGURE_MAP_HEADER,
    build_publish_run_config,
    parse_markdown,
    publish_dependency_error,
    resolve_figures,
    run_publish_workflow,
    validate_publish_result,
)
from reportmachine_runner.publish_contracts import source_hashes
from reportmachine_runner.publish_markdown import (
    CodeBlock,
    Heading,
    ImageBlock,
    ListBlock,
    Paragraph,
    Table,
    collect_links,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HAS_REPORTLAB = importlib.util.find_spec("reportlab") is not None
HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


class MarkdownParserTests(unittest.TestCase):
    def test_heading_levels_and_deep_heading_clamped(self) -> None:
        document = parse_markdown("# 제목\n\n## 절\n\n### 소절\n\n#### 더 깊은 절\n")
        headings = [block for block in document.blocks if isinstance(block, Heading)]
        self.assertEqual([h.level for h in headings], [1, 2, 3, 3])
        self.assertTrue(any("h4" in warning for warning in document.warnings))

    def test_paragraph_lines_are_joined(self) -> None:
        document = parse_markdown("첫 줄입니다.\n두 번째 줄입니다.\n\n다음 문단입니다.\n")
        paragraphs = [b for b in document.blocks if isinstance(b, Paragraph)]
        self.assertEqual(paragraphs[0].text, "첫 줄입니다. 두 번째 줄입니다.")
        self.assertEqual(paragraphs[1].text, "다음 문단입니다.")

    def test_unordered_and_ordered_lists_with_nesting(self) -> None:
        document = parse_markdown("- 하나\n  - 하위\n- 둘\n\n1. 첫째\n2. 둘째\n")
        lists = [b for b in document.blocks if isinstance(b, ListBlock)]
        self.assertEqual(len(lists), 2)
        self.assertEqual([item.depth for item in lists[0].items], [0, 1, 0])
        self.assertFalse(lists[0].items[0].ordered)
        self.assertTrue(lists[1].items[0].ordered)
        self.assertEqual([item.number for item in lists[1].items], [1, 2])

    def test_deep_list_nesting_is_clamped_with_warning(self) -> None:
        document = parse_markdown("- 하나\n      - 아주 깊은 항목\n")
        lists = [b for b in document.blocks if isinstance(b, ListBlock)]
        self.assertEqual(max(item.depth for item in lists[0].items), 2)
        self.assertTrue(any("list nesting" in warning for warning in document.warnings))

    def test_table_parsing(self) -> None:
        document = parse_markdown("| 항목 | 값 |\n| --- | --- |\n| 매출 | 1,024 |\n")
        tables = [b for b in document.blocks if isinstance(b, Table)]
        self.assertEqual(tables[0].header, ("항목", "값"))
        self.assertEqual(tables[0].rows, (("매출", "1,024"),))

    def test_blockquote_and_thematic_break_and_code_block(self) -> None:
        document = parse_markdown("> 인용입니다.\n\n---\n\n```python\nx = 1\n```\n")
        kinds = [type(block).__name__ for block in document.blocks]
        self.assertIn("BlockQuote", kinds)
        self.assertIn("ThematicBreak", kinds)
        code = next(b for b in document.blocks if isinstance(b, CodeBlock))
        self.assertEqual(code.lines, ("x = 1",))
        self.assertEqual(code.language, "python")

    def test_unclosed_code_fence_warns(self) -> None:
        document = parse_markdown("```\nx = 1\n")
        self.assertTrue(any("unclosed" in warning for warning in document.warnings))

    def test_block_image_is_its_own_block(self) -> None:
        document = parse_markdown(
            "문단입니다.\n\n![대체 텍스트](work/runs/r/images/a.png)\n\n다음.\n"
        )
        images = [b for b in document.blocks if isinstance(b, ImageBlock)]
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].alt, "대체 텍스트")
        self.assertEqual(images[0].target, "work/runs/r/images/a.png")
        self.assertEqual(document.image_targets(), ["work/runs/r/images/a.png"])

    def test_links_are_collected_in_first_appearance_order_and_deduped(self) -> None:
        document = parse_markdown(
            "본문 ([*보고서, 2026.01*](https://example.com/a)) 그리고 "
            "([*다른 보고서*](https://example.com/b)) 그리고 다시 "
            "([*보고서, 2026.01*](https://example.com/a))\n"
        )
        links = collect_links(document.blocks)
        self.assertEqual(
            [url for _, url in links], ["https://example.com/a", "https://example.com/b"]
        )
        self.assertEqual(links[0][0], "보고서, 2026.01")

    def test_unsupported_construct_is_reported_and_kept_as_text(self) -> None:
        document = parse_markdown('<div class="x">원문</div>\n')
        self.assertTrue(
            any("unsupported markdown construct" in warning for warning in document.warnings)
        )
        self.assertTrue(any(isinstance(block, Paragraph) for block in document.blocks))

    def test_real_repository_article_parses_without_warnings(self) -> None:
        article = REPO_ROOT / "outputs" / "sandisk_nand_china_20260729" / "article.md"
        if not article.is_file():
            self.skipTest("sample article output is not present in this checkout")
        document = parse_markdown(article.read_text(encoding="utf-8"))
        self.assertEqual(document.warnings, [])
        self.assertEqual(len(document.image_targets()), 6)


@unittest.skipUnless(HAS_REPORTLAB, "reportlab is not installed")
class InlineMarkupTests(unittest.TestCase):
    def test_html_special_characters_are_escaped(self) -> None:
        from reportmachine_runner.publish_pdf import render_inline

        rendered = render_inline("A & B <tag> 그리고 3 > 2")
        self.assertIn("&amp;", rendered)
        self.assertIn("&lt;tag&gt;", rendered)
        self.assertNotIn("<tag>", rendered)

    def test_strong_emphasis_and_code_are_converted(self) -> None:
        from reportmachine_runner.publish_pdf import render_inline

        rendered = render_inline("**굵게** 그리고 *기울임* 그리고 `코드`")
        self.assertIn("<b>굵게</b>", rendered)
        self.assertIn("<i>기울임</i>", rendered)
        self.assertIn("코드</font>", rendered)

    def test_link_gets_anchor_and_optional_footnote_number(self) -> None:
        from reportmachine_runner.publish_pdf import render_inline

        rendered = render_inline(
            "([*보고서*](https://example.com/a))",
            link_numbers={"https://example.com/a": 3},
        )
        self.assertIn('href="https://example.com/a"', rendered)
        self.assertIn("<super>[3]</super>", rendered)

    def test_inline_image_is_reduced_to_alt_text(self) -> None:
        from reportmachine_runner.publish_pdf import render_inline

        rendered = render_inline("앞 ![대체](a/b.png) 뒤")
        self.assertIn("대체", rendered)
        self.assertNotIn("a/b.png", rendered)


@unittest.skipUnless(HAS_REPORTLAB, "reportlab is not installed")
class FontResolutionTests(unittest.TestCase):
    def _sample_font(self) -> Path:
        import reportlab

        return Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"

    def test_configured_font_is_used(self) -> None:
        from reportmachine_runner.publish_pdf import resolve_fonts

        font = self._sample_font()
        fonts = resolve_fonts(PublishProfile(font_regular=str(font)), REPO_ROOT)
        self.assertEqual(fonts.regular_path, str(font))

    def test_missing_regular_font_raises_with_install_hint(self) -> None:
        from reportmachine_runner.publish_pdf import PublishFontError, resolve_fonts

        with patch("reportmachine_runner.publish_pdf.REGULAR_FONT_CANDIDATES", {}):
            with self.assertRaises(PublishFontError) as caught:
                resolve_fonts(PublishProfile(font_regular="/nonexistent/font.ttf"), REPO_ROOT)
        self.assertIn("no Hangul TrueType font found", str(caught.exception))

    def test_autodiscovery_finds_a_platform_font(self) -> None:
        from reportmachine_runner.publish_pdf import PublishFontError, resolve_fonts

        try:
            fonts = resolve_fonts(PublishProfile(), REPO_ROOT)
        except PublishFontError:
            self.skipTest("no platform Hangul font is installed in this environment")
        self.assertTrue(Path(fonts.regular_path).is_file())

    def test_bold_absent_falls_back_to_regular_family_mapping(self) -> None:
        from reportmachine_runner.publish_pdf import resolve_fonts

        font = self._sample_font()
        with patch("reportmachine_runner.publish_pdf.BOLD_FONT_CANDIDATES", {}):
            fonts = resolve_fonts(PublishProfile(font_regular=str(font)), REPO_ROOT)
        self.assertIsNone(fonts.bold_path)
        self.assertIsNone(fonts.as_manifest()["bold"])

    def test_render_path_never_registers_cid_fonts(self) -> None:
        from reportmachine_runner import publish_pdf

        source = Path(publish_pdf.__file__).read_text(encoding="utf-8")
        # CID fonts render Hangul as a blank page in poppler based viewers, so
        # they must never appear in the registration path.
        self.assertNotIn("UnicodeCIDFont(", source)
        for candidates in publish_pdf.REGULAR_FONT_CANDIDATES.values():
            for candidate in candidates:
                self.assertTrue(candidate.lower().endswith((".ttf", ".ttc", ".otf")), candidate)


class PublishFixtureMixin:
    def make_root(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / "project"
        root.mkdir(parents=True)
        return root

    def write_png(self, path: Path) -> None:
        # Minimal valid 2x2 truecolour PNG so figure placement can be
        # exercised without depending on sample article artifacts.
        path.write_bytes(
            bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000200000002080200"
                "0000fdd49a730000001049444154789c63b8f3f0351031402800"
                "4a320aa112d972940000000049454e44ae426082"
            )
        )

    def write_run(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        status: str = "pass",
        article_body: str | None = None,
        image_ledger: str | None = None,
        with_image: bool = False,
    ) -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if with_image:
            image_dir = root / "work" / "runs" / run_id / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            self.write_png(image_dir / "figure.png")
        body = article_body if article_body is not None else "# 제목\n\n본문입니다.\n"
        (output_dir / "article.md").write_text(body, encoding="utf-8")
        (output_dir / "source_ledger.csv").write_text(
            "source_id,url,local_path\nS001,https://example.com,inputs/source.md\n",
            encoding="utf-8",
        )
        (output_dir / "image_ledger.csv").write_text(
            image_ledger
            if image_ledger is not None
            else "image_path,inspected_status\n,not_available\n",
            encoding="utf-8",
        )
        (output_dir / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "industry",
                    "title": "제목",
                    "subtitle": "부제",
                    "one_sentence_insight": "핵심 문장입니다.",
                    "scorecard": None,
                    "output_files": {
                        "article_md": f"outputs/{run_id}/article.md",
                        "source_ledger_csv": f"outputs/{run_id}/source_ledger.csv",
                        "image_ledger_csv": f"outputs/{run_id}/image_ledger.csv",
                        "claim_ledger_json": f"outputs/{run_id}/claim_ledger.json",
                        "preflight_result_json": f"outputs/{run_id}/preflight_result.json",
                        "run_manifest_json": f"outputs/{run_id}/run_manifest.json",
                    },
                    "critical_warnings": [],
                    "checklist_status": status,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        schema_target = root / "schemas" / "publish_result.schema.json"
        schema_target.parent.mkdir(parents=True, exist_ok=True)
        schema_target.write_text(
            (REPO_ROOT / "schemas" / "publish_result.schema.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def config_for(self, root: Path, *, run_id: str = "test_run", **profile_kwargs):
        return build_publish_run_config(
            root=root.resolve(),
            run_id=run_id,
            profile=PublishProfile(**profile_kwargs),
        )

    def run_workflow(self, config, **kwargs) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = run_publish_workflow(config, **kwargs)
        return status, stdout.getvalue() + stderr.getvalue()

    def schema_patches(self) -> list:
        patches = [
            patch(
                "reportmachine_runner.publish_workflow.schema_validation_dependency_error",
                return_value=None,
            )
        ]
        if not HAS_JSONSCHEMA:
            patches.append(
                patch(
                    "reportmachine_runner.publish_workflow.validate_publish_result",
                    return_value=[],
                )
            )
        return patches

    def run_workflow_with_schema_patches(self, config, **kwargs) -> tuple[int, str]:
        patches = self.schema_patches()
        for active in patches:
            active.start()
        try:
            return self.run_workflow(config, **kwargs)
        finally:
            for active in patches:
                active.stop()


class FigureResolutionTests(PublishFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_root()

    def test_caption_prefers_ledger_caption(self) -> None:
        ledger = (
            "image_path,source_id,page_or_slide_number,caption,caption_candidate,keep_or_drop\n"
            "work/runs/test_run/images/figure.png,S009,31,원장 캡션,후보 캡션,keep\n"
        )
        self.write_run(self.root, image_ledger=ledger, with_image=True)
        config = self.config_for(self.root)
        figures, rows, warnings, errors = resolve_figures(
            config, ["work/runs/test_run/images/figure.png"]
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["caption_source"], "image_ledger.caption")
        self.assertIn("원장 캡션", rows[0]["caption"])
        self.assertIn("S009", rows[0]["caption"])
        self.assertIn("p.31", rows[0]["caption"])
        self.assertEqual(rows[0]["figure_number"], 1)
        self.assertEqual(len(figures), 1)

    def test_caption_falls_back_to_candidate(self) -> None:
        ledger = (
            "image_path,source_id,caption,caption_candidate,keep_or_drop\n"
            "work/runs/test_run/images/figure.png,S009,,후보 캡션,keep\n"
        )
        self.write_run(self.root, image_ledger=ledger, with_image=True)
        config = self.config_for(self.root)
        _, rows, _, errors = resolve_figures(config, ["work/runs/test_run/images/figure.png"])
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["caption_source"], "image_ledger.caption_candidate")

    def test_dropped_image_reference_warns_but_still_renders(self) -> None:
        ledger = (
            "image_path,source_id,caption,keep_or_drop,drop_reason\n"
            "work/runs/test_run/images/figure.png,S020,캡션,drop,불필요\n"
        )
        self.write_run(self.root, image_ledger=ledger, with_image=True)
        config = self.config_for(self.root)
        figures, rows, warnings, errors = resolve_figures(
            config, ["work/runs/test_run/images/figure.png"]
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(figures), 1)
        self.assertTrue(any("keep_or_drop=drop" in warning for warning in warnings))

    def test_missing_image_file_is_an_error(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root)
        _, _, _, errors = resolve_figures(config, ["work/runs/test_run/images/absent.png"])
        self.assertTrue(any("missing image" in error for error in errors))

    def test_image_outside_repo_root_is_an_error(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root)
        _, _, _, errors = resolve_figures(config, ["../../../etc/passwd"])
        self.assertTrue(any("not repo-local" in error for error in errors))

    def test_image_absent_from_ledger_warns(self) -> None:
        self.write_run(self.root, with_image=True)
        config = self.config_for(self.root)
        _, rows, warnings, errors = resolve_figures(
            config, ["work/runs/test_run/images/figure.png"]
        )
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["caption_source"], "article_alt_text")
        self.assertTrue(any("not listed in the image ledger" in w for w in warnings))

    def test_figure_numbers_follow_article_reference_order(self) -> None:
        self.write_run(self.root, with_image=True)
        image_dir = self.root / "work" / "runs" / "test_run" / "images"
        self.write_png(image_dir / "second.png")
        config = self.config_for(self.root)
        _, rows, _, errors = resolve_figures(
            config,
            [
                "work/runs/test_run/images/second.png",
                "work/runs/test_run/images/figure.png",
            ],
        )
        self.assertEqual(errors, [])
        self.assertEqual([row["figure_number"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["image_path"], "work/runs/test_run/images/second.png")


class PublishWorkflowGuardTests(PublishFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_root()

    def test_missing_inputs_fail_before_rendering(self) -> None:
        config = self.config_for(self.root)
        status, output = self.run_workflow(config)
        self.assertEqual(status, 2)
        self.assertIn("missing article", output)

    def test_smoke_test_resolves_inputs_without_rendering(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root)
        status, output = self.run_workflow(config, smoke_test=True)
        self.assertEqual(status, 0, output)
        self.assertIn("publish smoke-test", output)
        self.assertFalse((self.root / "outputs/test_run/publish/article.pdf").exists())

    def test_failed_article_status_skips_pdf_and_writes_failure_result(self) -> None:
        self.write_run(self.root, status="fail")
        config = self.config_for(self.root)
        with patch(
            "reportmachine_runner.publish_workflow.publish_dependency_error", return_value=None
        ), patch(
            "reportmachine_runner.publish_workflow.schema_validation_dependency_error",
            return_value=None,
        ):
            status, output = self.run_workflow(config)
        self.assertEqual(status, 2)
        self.assertIn("checklist_status is fail", output)
        payload = json.loads(
            (self.root / "outputs/test_run/publish/publish_result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["checklist_status"], "fail")
        self.assertEqual(payload["page_count"], 0)
        self.assertFalse((self.root / "outputs/test_run/publish/article.pdf").exists())

    def test_renderer_dependency_error_stops_the_run(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root)
        with patch(
            "reportmachine_runner.publish_workflow.publish_dependency_error",
            return_value="missing optional publish dependency: reportlab",
        ):
            status, output = self.run_workflow(config)
        self.assertEqual(status, 2)
        self.assertIn("reportlab", output)

    def test_schema_dependency_error_stops_before_rendering(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root)
        with patch(
            "reportmachine_runner.publish_workflow.publish_dependency_error", return_value=None
        ), patch(
            "reportmachine_runner.publish_workflow.schema_validation_dependency_error",
            return_value="missing required runtime dependency: jsonschema",
        ):
            status, output = self.run_workflow(config)
        self.assertEqual(status, 2)
        self.assertIn("jsonschema", output)
        self.assertFalse((self.root / "outputs/test_run/publish/article.pdf").exists())

    def test_publish_dependency_error_mentions_publish_extra(self) -> None:
        with patch("importlib.util.find_spec", return_value=None):
            message = publish_dependency_error()
        self.assertIsNotNone(message)
        assert message is not None
        self.assertIn("[publish]", message)


@unittest.skipUnless(HAS_REPORTLAB, "reportlab is not installed")
class PublishRenderTests(PublishFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_root()
        import reportlab

        self.font = str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")

    def test_render_produces_pdf_manifest_and_figure_map(self) -> None:
        article = (
            "# 제목입니다\n\n"
            "본문 문단입니다. 인용은 ([*보고서, 2026.01*](https://example.com/a)) 형태입니다.\n\n"
            "## 절 제목\n\n"
            "![도표](work/runs/test_run/images/figure.png)\n\n"
            "다음 문단입니다.\n"
        )
        ledger = (
            "image_path,source_id,page_or_slide_number,caption,keep_or_drop\n"
            "work/runs/test_run/images/figure.png,S009,31,원장 캡션,keep\n"
        )
        self.write_run(self.root, article_body=article, image_ledger=ledger, with_image=True)
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)

        pdf = self.root / "outputs/test_run/publish/article.pdf"
        self.assertTrue(pdf.is_file())
        self.assertGreater(pdf.stat().st_size, 0)

        manifest = json.loads(
            (self.root / "outputs/test_run/publish/publish_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["render_surface"], "local_deterministic_renderer")
        self.assertEqual(manifest["figure_count"], 1)
        self.assertGreater(manifest["page_count"], 0)

        with (self.root / "outputs/test_run/publish/publish_figure_map.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(list(rows[0].keys()), PUBLISH_FIGURE_MAP_HEADER)
        self.assertEqual(rows[0]["figure_number"], "1")

        result = json.loads(
            (self.root / "outputs/test_run/publish/publish_result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn(result["checklist_status"], {"pass", "pass_with_warnings"})
        self.assertEqual(result["figure_count"], 1)
        self.assertEqual(result["render_surface"], "local_deterministic_renderer")
        self.assertEqual(result["font_regular"], self.font)

    def test_missing_image_fails_render_and_leaves_no_pdf(self) -> None:
        article = "# 제목\n\n![없는 그림](work/runs/test_run/images/absent.png)\n"
        self.write_run(self.root, article_body=article)
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 2)
        self.assertIn("missing image", output)
        self.assertFalse((self.root / "outputs/test_run/publish/article.pdf").exists())

    def test_font_failure_writes_failure_result_without_pdf(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root, font_regular="/nonexistent/font.ttf")
        with patch("reportmachine_runner.publish_pdf.REGULAR_FONT_CANDIDATES", {}):
            status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 2)
        self.assertIn("no Hangul TrueType font found", output)
        self.assertFalse((self.root / "outputs/test_run/publish/article.pdf").exists())
        payload = json.loads(
            (self.root / "outputs/test_run/publish/publish_result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload["checklist_status"], "fail")

    def test_article_and_ledgers_are_never_modified_by_publishing(self) -> None:
        self.write_run(self.root, with_image=True)
        article_path = self.root / "outputs/test_run/article.md"
        ledger_path = self.root / "outputs/test_run/image_ledger.csv"
        before_article = article_path.read_bytes()
        before_ledger = ledger_path.read_bytes()
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)
        self.assertEqual(article_path.read_bytes(), before_article)
        self.assertEqual(ledger_path.read_bytes(), before_ledger)

    def test_mirror_latest_copies_publish_directory(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=True)
        self.assertEqual(status, 0, output)
        self.assertTrue((self.root / "outputs/latest/publish/article.pdf").is_file())

    def test_input_changed_during_render_fails(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root, font_regular=self.font)

        from reportmachine_runner import publish_workflow as module

        real_parse = module.parse_markdown

        def parse_and_mutate(text: str):
            document = real_parse(text)
            (self.root / "outputs/test_run/article.md").write_text(
                "# 제목\n\n렌더 중 변경되었습니다.\n", encoding="utf-8"
            )
            return document

        with patch.object(module, "parse_markdown", side_effect=parse_and_mutate):
            status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 2)
        self.assertIn("changed during rendering", output)

    def test_cover_and_toc_can_be_disabled(self) -> None:
        self.write_run(self.root, article_body="# 제목\n\n## 절\n\n본문입니다.\n")
        config = self.config_for(
            self.root,
            font_regular=self.font,
            include_cover=False,
            include_toc=False,
            include_link_footnotes=False,
        )
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)
        self.assertTrue((self.root / "outputs/test_run/publish/article.pdf").is_file())

    def test_repeated_renders_are_byte_identical(self) -> None:
        import hashlib

        self.write_run(self.root, with_image=True)
        config = self.config_for(self.root, font_regular=self.font)
        pdf = self.root / "outputs/test_run/publish/article.pdf"

        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)
        first = hashlib.sha256(pdf.read_bytes()).hexdigest()

        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)
        second = hashlib.sha256(pdf.read_bytes()).hexdigest()

        # A deterministic renderer must not embed wall-clock timestamps or a
        # random document /ID.
        self.assertEqual(first, second)

    @unittest.skipUnless(HAS_PYPDF, "pypdf is not installed")
    def test_rendered_pdf_contains_extractable_korean_text(self) -> None:
        import pypdf

        from reportmachine_runner.publish_pdf import PublishFontError, resolve_fonts

        try:
            resolve_fonts(PublishProfile(), REPO_ROOT)
        except PublishFontError:
            self.skipTest("no platform Hangul font is installed in this environment")

        article = (
            "# 한글 제목입니다\n\n"
            "본문에 한글이 정상적으로 들어가야 합니다. 숫자는 1,024와 12.5% 입니다.\n"
        )
        self.write_run(self.root, article_body=article)
        # Auto-discovered platform font, not the ASCII-only bundled test font.
        config = self.config_for(self.root)
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)

        reader = pypdf.PdfReader(str(self.root / "outputs/test_run/publish/article.pdf"))
        self.assertGreater(len(reader.pages), 0)
        text = "".join(page.extract_text() or "" for page in reader.pages)
        # The cover renders the ledger title, so assert on body prose and on the
        # cover metadata that both must survive font embedding.
        self.assertIn("본문에 한글이 정상적으로 들어가야 합니다", text)
        self.assertIn("핵심 문장입니다", text)
        self.assertIn("1,024", text)
        # A missing-glyph regression renders Hangul as .notdef boxes.
        self.assertNotIn("■", text)

    @unittest.skipUnless(HAS_PYPDF, "pypdf is not installed")
    def test_figure_count_matches_article_image_references(self) -> None:
        import pypdf

        article = (
            "# 제목\n\n"
            "![첫 그림](work/runs/test_run/images/figure.png)\n\n"
            "본문입니다.\n\n"
            "![둘째 그림](work/runs/test_run/images/second.png)\n"
        )
        self.write_run(self.root, article_body=article, with_image=True)
        self.write_png(self.root / "work" / "runs" / "test_run" / "images" / "second.png")
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow_with_schema_patches(config, mirror_latest=False)
        self.assertEqual(status, 0, output)

        result = json.loads(
            (self.root / "outputs/test_run/publish/publish_result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(result["figure_count"], 2)
        reader = pypdf.PdfReader(str(self.root / "outputs/test_run/publish/article.pdf"))
        embedded = sum(len(page.images) for page in reader.pages)
        self.assertEqual(embedded, 2)


@unittest.skipUnless(HAS_REPORTLAB and HAS_JSONSCHEMA, "publish render dependencies missing")
class PublishResultValidationTests(PublishFixtureMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.root = self.make_root()
        import reportlab

        self.font = str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf")

    def test_result_matches_schema_after_successful_render(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow(config, mirror_latest=False)
        self.assertEqual(status, 0, output)
        self.assertEqual(validate_publish_result(config, expected_hashes=source_hashes(config)), [])

    def test_hash_mismatch_is_reported(self) -> None:
        self.write_run(self.root)
        config = self.config_for(self.root, font_regular=self.font)
        status, output = self.run_workflow(config, mirror_latest=False)
        self.assertEqual(status, 0, output)
        errors = validate_publish_result(
            config,
            expected_hashes={"article_sha256": "0" * 64, "article_result_sha256": "0" * 64},
        )
        self.assertTrue(any("sha256" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
