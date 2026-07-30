from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner.source_ingest import (
    SourceIngestConfig,
    load_source_ingest_result,
    run_research_candidate_ingest,
    run_source_ingest,
)


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

    def research_candidate(
        self,
        candidate_id: str,
        *,
        url: str | None = None,
        local_path: str | None = None,
    ) -> dict:
        return {
            "candidate_id": candidate_id,
            "search_intent": "confirming",
            "title": candidate_id,
            "url": url,
            "local_path": local_path,
            "why_relevant": "identity test",
        }

    def not_found_candidate(
        self,
        candidate_id: str,
        *,
        url: str | None = None,
        local_path: str | None = None,
        source_id: str | None = None,
    ) -> dict:
        candidate = {
            "candidate_id": candidate_id,
            "search_intent": "confirming",
            "title": candidate_id,
            "url": url,
            "local_path": local_path,
            "why_relevant": "absence test",
            "not_found": True,
            "search_log": [{"query": candidate_id, "scope": "issuer site"}],
        }
        if source_id is not None:
            candidate["source_id"] = source_id
        return candidate

    def ingest_pair(self, root: Path, config, primary: dict, counter: dict):
        research = root / "work" / "runs" / "test_run" / "research"
        research.mkdir(parents=True, exist_ok=True)
        (research / "source_candidates.primary.jsonl").write_text(
            json.dumps(primary, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (research / "source_candidates.counter.jsonl").write_text(
            json.dumps(counter, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return run_research_candidate_ingest(
            config,
            candidate_paths=(
                "work/runs/test_run/research/source_candidates.primary.jsonl",
                "work/runs/test_run/research/source_candidates.counter.jsonl",
            ),
        )

    def assert_no_inherited_material(self, row: dict) -> None:
        self.assertEqual(row["ingest_status"], "remote_unverified")
        self.assertIsNone(row["sha256"])
        self.assertIsNone(row["ingested_local_path"])
        self.assertIsNone(row["extracted_text_path"])
        self.assertIsNone(row["text_status"])

    def test_not_found_candidate_does_not_inherit_same_candidate_id_source(self) -> None:
        # A `not_found` outcome has no URL or local path, so it must never adopt the
        # downloaded material of a different candidate that shares its lane-local id.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)

            with patch(
                "urllib.request.urlopen",
                return_value=FakeResponse(b"<html><body>real</body></html>", "text/html"),
            ) as urlopen:
                result = self.ingest_pair(
                    root,
                    config,
                    self.research_candidate(
                        "CAND-1", url="https://www.sec.gov/files/real.html"
                    ),
                    self.not_found_candidate("CAND-1"),
                )

            self.assertEqual(result.errors, ())
            urlopen.assert_called_once()
            research = root / "work" / "runs" / "test_run" / "research"
            found = json.loads(
                (research / "source_candidates.primary.jsonl").read_text(encoding="utf-8")
            )
            absent = json.loads(
                (research / "source_candidates.counter.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(found["ingest_status"], "downloaded")
            self.assertNotEqual(absent["source_id"], found["source_id"])
            self.assert_no_inherited_material(absent)

    def test_identityless_candidate_cannot_alias_a_supplied_source_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)

            with patch(
                "urllib.request.urlopen",
                return_value=FakeResponse(b"<html><body>real</body></html>", "text/html"),
            ):
                result = self.ingest_pair(
                    root,
                    config,
                    self.research_candidate(
                        "CAND-1", url="https://www.sec.gov/files/real.html"
                    ),
                    self.not_found_candidate("CAND-9", source_id="CAND-1"),
                )

            self.assertEqual(result.errors, ())
            research = root / "work" / "runs" / "test_run" / "research"
            found = json.loads(
                (research / "source_candidates.primary.jsonl").read_text(encoding="utf-8")
            )
            absent = json.loads(
                (research / "source_candidates.counter.jsonl").read_text(encoding="utf-8")
            )
            self.assertNotEqual(absent["source_id"], found["source_id"])
            self.assert_no_inherited_material(absent)

    def test_identityless_candidate_ingest_is_idempotent(self) -> None:
        # Without a runner-owned assignment table, a second ingest of the same absence
        # row would allocate a phantom `_2` source every time the file is re-processed.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)
            with patch(
                "urllib.request.urlopen",
                return_value=FakeResponse(b"<html><body>real</body></html>", "text/html"),
            ):
                self.ingest_pair(
                    root,
                    config,
                    self.research_candidate(
                        "CAND-1", url="https://www.sec.gov/files/real.html"
                    ),
                    self.not_found_candidate("CAND-1"),
                )
            research = root / "work" / "runs" / "test_run" / "research"
            counter_path = research / "source_candidates.counter.jsonl"
            first = json.loads(counter_path.read_text(encoding="utf-8"))

            with patch("urllib.request.urlopen") as urlopen:
                repeated = run_research_candidate_ingest(
                    config,
                    candidate_paths=(
                        "work/runs/test_run/research/source_candidates.primary.jsonl",
                        "work/runs/test_run/research/source_candidates.counter.jsonl",
                    ),
                )

            urlopen.assert_not_called()
            self.assertEqual(repeated.errors, ())
            second = json.loads(counter_path.read_text(encoding="utf-8"))
            self.assertEqual(second["source_id"], first["source_id"])
            self.assert_no_inherited_material(second)
            ledger = root / "work/runs/test_run/ledgers/source_ledger.candidate.csv"
            with ledger.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 2)

    def test_blank_and_whitespace_identities_stay_distinct_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root)
            run_source_ingest(config)

            result = self.ingest_pair(
                root,
                config,
                self.not_found_candidate("CAND-1", url="", local_path=""),
                self.not_found_candidate("CAND-1", url="   ", local_path="  "),
            )

            self.assertEqual(result.errors, ())
            research = root / "work" / "runs" / "test_run" / "research"
            first = json.loads(
                (research / "source_candidates.primary.jsonl").read_text(encoding="utf-8")
            )
            second = json.loads(
                (research / "source_candidates.counter.jsonl").read_text(encoding="utf-8")
            )
            self.assertNotEqual(first["source_id"], second["source_id"])
            self.assert_no_inherited_material(first)
            self.assert_no_inherited_material(second)

    def test_assignment_pointing_at_a_materialized_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)
            with patch(
                "urllib.request.urlopen",
                return_value=FakeResponse(b"<html><body>real</body></html>", "text/html"),
            ):
                self.ingest_pair(
                    root,
                    config,
                    self.research_candidate(
                        "CAND-1", url="https://www.sec.gov/files/real.html"
                    ),
                    self.not_found_candidate("CAND-1"),
                )
            manifest_path = root / "work/runs/test_run/research/source_ingest_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            key = next(iter(manifest["candidate_assignments"]))
            manifest["candidate_assignments"][key] = "CAND-1"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            ledger = root / "work/runs/test_run/ledgers/source_ledger.candidate.csv"
            before = ledger.read_bytes()

            result = run_research_candidate_ingest(
                config,
                candidate_paths=(
                    "work/runs/test_run/research/source_candidates.primary.jsonl",
                    "work/runs/test_run/research/source_candidates.counter.jsonl",
                ),
            )

            self.assertTrue(result.errors)
            self.assertIn("materialized source id", result.errors[0])
            self.assertEqual(ledger.read_bytes(), before)

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

    def test_network_disabled_remote_source_is_remote_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(
                root,
                [
                    {
                        "source_id": "sec_remote",
                        "article_type": "earnings",
                        "doc_kind": "filing",
                        "url": "https://www.sec.gov/files/acme.html",
                        "source_tier": "primary",
                        "required": "true",
                    }
                ],
            )

            result = run_source_ingest(self.base_config(root, allow_downloads=False))

            self.assertEqual(result.manifest["records"][0]["ingest_status"], "remote_unverified")
            self.assertEqual(result.manifest["counts"]["blocked_sources"], 1)

    def test_extracts_visible_html_and_discards_hidden_or_executable_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html = root / "inputs" / "release.html"
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text(
                """
                <html>
                  <head><title>metadata title</title><style>.x { display: none }</style></head>
                  <body>
                    <!-- hidden comment instruction -->
                    <script>embedded prompt instruction</script>
                    <p>Visible revenue increased 12 percent.</p>
                    <div hidden>hidden attribute text</div>
                    <div aria-hidden="true">aria hidden text</div>
                    <div style="display: none">inline hidden text</div>
                  </body>
                </html>
                """,
                encoding="utf-8",
            )
            self.write_sources(
                root,
                [
                    {
                        "source_id": "release",
                        "article_type": "earnings",
                        "doc_kind": "earnings_release",
                        "local_path": "inputs/release.html",
                        "source_tier": "primary",
                        "required": "true",
                    }
                ],
            )

            result = run_source_ingest(self.base_config(root))

            record = result.manifest["records"][0]
            self.assertEqual(record["text_status"], "ok")
            extracted = (root / record["extracted_text_path"]).read_text(encoding="utf-8")
            self.assertIn("Visible revenue increased 12 percent.", extracted)
            for forbidden in (
                "metadata title",
                "hidden comment instruction",
                "embedded prompt instruction",
                "hidden attribute text",
                "aria hidden text",
                "inline hidden text",
            ):
                self.assertNotIn(forbidden, extracted)

    def test_research_candidate_is_downloaded_hashed_extracted_and_enriched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)
            candidate_path = root / "work/runs/test_run/research/source_candidates.primary.jsonl"
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "S900",
                        "lane_id": "filings",
                        "question_ids": ["Q1"],
                        "search_intent": "confirming",
                        "title": "Acme filing",
                        "url": "https://www.sec.gov/files/acme",
                        "local_path": None,
                        "issuer": "Acme",
                        "source_role": "official filing",
                        "doc_kind": "filing",
                        "publication_date": "2026-07-29",
                        "why_relevant": "Direct filing",
                        "expected_evidence_class": "issuer_claim",
                        "discovery_method": "web search",
                        "not_found": False,
                        "search_log": [],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            content = b"<html><body><p>Acme production started.</p><script>ignore me</script></body></html>"

            with patch(
                "urllib.request.urlopen",
                return_value=FakeResponse(content, content_type="text/html"),
            ):
                result = run_research_candidate_ingest(
                    config,
                    candidate_paths=(
                        "work/runs/test_run/research/source_candidates.primary.jsonl",
                    ),
                )

            self.assertEqual(result.errors, ())
            enriched = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertEqual(enriched["source_id"], "S900")
            self.assertEqual(enriched["ingest_status"], "downloaded")
            self.assertTrue(enriched["sha256"])
            self.assertTrue(enriched["ingested_local_path"])
            self.assertTrue(enriched["extracted_text_path"])
            extracted = (root / enriched["extracted_text_path"]).read_text(encoding="utf-8")
            self.assertIn("Acme production started.", extracted)
            self.assertNotIn("ignore me", extracted)
            ledger = (root / "work/runs/test_run/ledgers/source_ledger.candidate.csv").read_text(
                encoding="utf-8"
            )
            self.assertIn("S900", ledger)
            self.assertEqual(result.manifest["counts"]["research_sources_added"], 1)

            loaded = load_source_ingest_result(config)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.manifest["counts"]["research_sources_added"], 1)

            with patch("urllib.request.urlopen") as urlopen:
                repeated = run_research_candidate_ingest(
                    config,
                    candidate_paths=(
                        "work/runs/test_run/research/source_candidates.primary.jsonl",
                    ),
                )
            urlopen.assert_not_called()
            self.assertEqual(repeated.manifest["counts"]["research_sources_added"], 1)
            with (
                root / "work/runs/test_run/ledgers/source_ledger.candidate.csv"
            ).open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_research_candidate_cannot_register_an_arbitrary_repo_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            prompt = root / "prompts" / "PROMPT.md"
            prompt.parent.mkdir(parents=True, exist_ok=True)
            prompt.write_text("repository instruction, not evidence\n", encoding="utf-8")
            config = self.base_config(root)
            run_source_ingest(config)
            candidate_path = root / "work/runs/test_run/research/source_candidates.primary.jsonl"
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text(
                json.dumps(
                    {
                        "candidate_id": "LOCAL-1",
                        "search_intent": "confirming",
                        "title": "unsafe local candidate",
                        "url": None,
                        "local_path": "prompts/PROMPT.md",
                        "why_relevant": "should not be trusted",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_research_candidate_ingest(
                config,
                candidate_paths=(
                    "work/runs/test_run/research/source_candidates.primary.jsonl",
                ),
            )

            self.assertEqual(result.errors, ())
            enriched = json.loads(candidate_path.read_text(encoding="utf-8"))
            self.assertEqual(enriched["ingest_status"], "blocked")
            self.assertIsNone(enriched["ingested_local_path"])
            self.assertIn("inputs/sources.csv", enriched["ingest_reason"])

    def test_same_candidate_id_with_different_urls_creates_distinct_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)
            research = root / "work" / "runs" / "test_run" / "research"
            primary = research / "source_candidates.primary.jsonl"
            counter = research / "source_candidates.counter.jsonl"
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text(
                json.dumps(
                    self.research_candidate(
                        "CAND-1",
                        url="https://www.sec.gov/files/first.html",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            counter.write_text(
                json.dumps(
                    self.research_candidate(
                        "CAND-1",
                        url="https://www.sec.gov/files/second.html",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            responses = [
                FakeResponse(b"<html><body>first source</body></html>", "text/html"),
                FakeResponse(b"<html><body>second source</body></html>", "text/html"),
            ]

            with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
                result = run_research_candidate_ingest(
                    config,
                    candidate_paths=(
                        "work/runs/test_run/research/source_candidates.primary.jsonl",
                        "work/runs/test_run/research/source_candidates.counter.jsonl",
                    ),
                )

            self.assertEqual(result.errors, ())
            self.assertEqual(urlopen.call_count, 2)
            first = json.loads(primary.read_text(encoding="utf-8"))
            second = json.loads(counter.read_text(encoding="utf-8"))
            self.assertNotEqual(first["source_id"], second["source_id"])
            self.assertNotEqual(first["sha256"], second["sha256"])

    def test_same_url_with_different_candidate_ids_reuses_one_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self.write_sources(root, [])
            config = self.base_config(root, allow_downloads=True)
            run_source_ingest(config)
            research = root / "work" / "runs" / "test_run" / "research"
            primary = research / "source_candidates.primary.jsonl"
            counter = research / "source_candidates.counter.jsonl"
            primary.parent.mkdir(parents=True, exist_ok=True)
            url = "https://www.sec.gov/files/shared.html"
            primary.write_text(
                json.dumps(self.research_candidate("CAND-1", url=url)) + "\n",
                encoding="utf-8",
            )
            counter.write_text(
                json.dumps(self.research_candidate("CAND-2", url=url)) + "\n",
                encoding="utf-8",
            )

            with patch(
                "urllib.request.urlopen",
                return_value=FakeResponse(b"<html><body>shared</body></html>", "text/html"),
            ) as urlopen:
                result = run_research_candidate_ingest(
                    config,
                    candidate_paths=(
                        "work/runs/test_run/research/source_candidates.primary.jsonl",
                        "work/runs/test_run/research/source_candidates.counter.jsonl",
                    ),
                )

            self.assertEqual(result.errors, ())
            urlopen.assert_called_once()
            first = json.loads(primary.read_text(encoding="utf-8"))
            second = json.loads(counter.read_text(encoding="utf-8"))
            self.assertEqual(first["source_id"], second["source_id"])
            self.assertEqual(first["sha256"], second["sha256"])

    def test_same_candidate_id_with_different_local_paths_creates_distinct_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_local = root / "inputs" / "first.html"
            second_local = root / "inputs" / "second.html"
            first_local.parent.mkdir(parents=True, exist_ok=True)
            first_local.write_text("first local source", encoding="utf-8")
            second_local.write_text("second local source", encoding="utf-8")
            self.write_sources(
                root,
                [
                    {
                        "source_id": "LOCAL-1",
                        "article_type": "earnings",
                        "local_path": "inputs/first.html",
                        "source_tier": "primary",
                        "required": "false",
                    },
                    {
                        "source_id": "LOCAL-2",
                        "article_type": "earnings",
                        "local_path": "inputs/second.html",
                        "source_tier": "primary",
                        "required": "false",
                    },
                ],
            )
            config = self.base_config(root)
            run_source_ingest(config)
            research = root / "work" / "runs" / "test_run" / "research"
            primary = research / "source_candidates.primary.jsonl"
            counter = research / "source_candidates.counter.jsonl"
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary.write_text(
                json.dumps(
                    self.research_candidate(
                        "CAND-1",
                        local_path="inputs/first.html",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            counter.write_text(
                json.dumps(
                    self.research_candidate(
                        "CAND-1",
                        local_path="inputs/second.html",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_research_candidate_ingest(
                config,
                candidate_paths=(
                    "work/runs/test_run/research/source_candidates.primary.jsonl",
                    "work/runs/test_run/research/source_candidates.counter.jsonl",
                ),
            )

            self.assertEqual(result.errors, ())
            first = json.loads(primary.read_text(encoding="utf-8"))
            second = json.loads(counter.read_text(encoding="utf-8"))
            self.assertNotEqual(first["source_id"], second["source_id"])
            self.assertNotEqual(first["sha256"], second["sha256"])

    def test_candidate_url_and_local_path_cannot_resolve_to_different_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local = root / "inputs" / "local.html"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text("local source", encoding="utf-8")
            url = "https://www.sec.gov/files/remote.html"
            self.write_sources(
                root,
                [
                    {
                        "source_id": "remote",
                        "article_type": "earnings",
                        "url": url,
                        "source_tier": "primary",
                        "required": "false",
                    },
                    {
                        "source_id": "local",
                        "article_type": "earnings",
                        "local_path": "inputs/local.html",
                        "source_tier": "primary",
                        "required": "false",
                    },
                ],
            )
            config = self.base_config(root)
            run_source_ingest(config)
            candidate = root / "work/runs/test_run/research/source_candidates.primary.jsonl"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(
                json.dumps(
                    self.research_candidate(
                        "CAND-1",
                        url=url,
                        local_path="inputs/local.html",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            ledger = root / config.work_run_dir / "ledgers" / "source_ledger.candidate.csv"
            before_candidate = candidate.read_bytes()
            before_ledger = ledger.read_bytes()

            with patch("urllib.request.urlopen") as urlopen:
                result = run_research_candidate_ingest(
                    config,
                    candidate_paths=(
                        "work/runs/test_run/research/source_candidates.primary.jsonl",
                    ),
                )

            urlopen.assert_not_called()
            self.assertTrue(result.errors)
            self.assertIn("conflicting URL/local path identity", result.errors[0])
            self.assertEqual(candidate.read_bytes(), before_candidate)
            self.assertEqual(ledger.read_bytes(), before_ledger)

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
