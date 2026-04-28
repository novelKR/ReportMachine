from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.source_ingest import SourceIngestConfig, run_source_ingest


SOURCES_HEADER = [
    "source_id",
    "article_type",
    "company",
    "period_role",
    "period",
    "doc_kind",
    "issuer",
    "date",
    "title",
    "url",
    "source_tier",
    "required",
    "local_path",
    "notes",
]


class FakeResponse:
    def __init__(self, content: bytes, content_type: str = "application/pdf") -> None:
        self._buffer = io.BytesIO(content)
        self.headers = {"Content-Type": content_type}
        self.status = 200

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class SourceIngestTests(unittest.TestCase):
    def write_sources(self, root: Path, rows: list[dict[str, str]]) -> None:
        inputs = root / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        with (inputs / "sources.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SOURCES_HEADER)
            writer.writeheader()
            writer.writerows(rows)

    def base_config(self, root: Path, *, allow_downloads: bool = False) -> SourceIngestConfig:
        return SourceIngestConfig(
            root=root,
            mode="earnings",
            run_id="test_run",
            sources_path="inputs/sources.csv",
            work_run_dir="work/runs/test_run",
            allow_downloads=allow_downloads,
        )

    def test_blocks_unallowed_https_domain_without_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(
                root,
                [
                    {
                        "source_id": "vendor_pdf",
                        "article_type": "earnings",
                        "doc_kind": "ir_deck",
                        "url": "https://cdn.example.com/deck.pdf",
                        "source_tier": "primary",
                        "required": "true",
                    }
                ],
            )

            with patch("urllib.request.urlopen") as urlopen:
                result = run_source_ingest(self.base_config(root, allow_downloads=True))

            urlopen.assert_not_called()
            self.assertEqual(result.manifest["counts"]["blocked_sources"], 1)
            ledger = (root / "work/runs/test_run/ledgers/source_ledger.candidate.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("vendor_pdf", ledger)
            self.assertIn("domain is not in the official/allowed source list", ledger)

    def test_downloads_allowed_pdf_and_records_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\n%%EOF\n"
            self.write_sources(
                root,
                [
                    {
                        "source_id": "sec_pdf",
                        "article_type": "earnings",
                        "doc_kind": "filing",
                        "url": "https://www.sec.gov/files/acme.pdf",
                        "source_tier": "primary",
                        "required": "true",
                    }
                ],
            )

            with patch("urllib.request.urlopen", return_value=FakeResponse(content)):
                result = run_source_ingest(self.base_config(root, allow_downloads=True))

            digest = hashlib.sha256(content).hexdigest()
            self.assertEqual(result.manifest["counts"]["ingested_sources"], 1)
            record = result.manifest["records"][0]
            self.assertEqual(record["sha256"], digest)
            self.assertEqual(record["mime_type"], "application/pdf")
            self.assertEqual(record["ingest_status"], "downloaded")
            self.assertTrue((root / record["local_path"]).is_file())
            self.assertEqual(result.manifest["policy"]["openai_file_search"], "disabled")

    def test_indexes_pdf_pages_and_returns_rendered_image_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "inputs" / "deck.pdf"
            pdf.parent.mkdir(parents=True, exist_ok=True)
            pdf.write_bytes(b"%PDF-1.4\n/Type /Page\n/Type /Page\n")
            self.write_sources(
                root,
                [
                    {
                        "source_id": "deck",
                        "article_type": "earnings",
                        "doc_kind": "ir_deck",
                        "local_path": "inputs/deck.pdf",
                        "source_tier": "primary",
                        "required": "true",
                    }
                ],
            )

            def fake_extract(_pdf_path: Path, page: int, text_path: Path) -> tuple[str, str]:
                text = f"page {page} revenue bridge"
                text_path.parent.mkdir(parents=True, exist_ok=True)
                text_path.write_text(text, encoding="utf-8")
                return "ok", text

            def fake_render(
                *,
                root: Path,
                pdf_path: Path,
                source_id: str,
                page: int,
                output_dir: Path,
                dpi: int,
            ) -> tuple[str, str, str, str]:
                return (
                    f"work/runs/test_run/images/rendered/{source_id}_page_{page:03d}.png",
                    f"imagehash-{page}",
                    "ok",
                    "",
                )

            config = SourceIngestConfig(
                root=root,
                mode="earnings",
                run_id="test_run",
                sources_path="inputs/sources.csv",
                work_run_dir="work/runs/test_run",
                allow_downloads=False,
                max_image_inputs=1,
            )
            with (
                patch("reportmachine_runner.source_ingest._pdf_page_count", return_value=2),
                patch("reportmachine_runner.source_ingest._extract_pdf_page_text", side_effect=fake_extract),
                patch("reportmachine_runner.source_ingest._render_pdf_page", side_effect=fake_render),
            ):
                result = run_source_ingest(config)

            self.assertEqual(result.image_inputs, ("work/runs/test_run/images/rendered/deck_page_001.png",))
            index_lines = (root / "work/runs/test_run/research/pdf_index.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(len(index_lines), 2)
            first_index = json.loads(index_lines[0])
            self.assertEqual(first_index["text_status"], "ok")
            self.assertEqual(first_index["render_status"], "ok")
            self.assertIn("revenue bridge", first_index["excerpt"])
            self.assertEqual(first_index["term_frequency"]["revenue"], 1)
            self.assertGreater(first_index["token_count"], 0)


if __name__ == "__main__":
    unittest.main()
