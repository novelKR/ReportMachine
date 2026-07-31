from __future__ import annotations

import csv
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from reportmachine_runner import cli
from reportmachine_runner import workflow as workflow_module
from reportmachine_runner.intake import SOURCE_DISCOVERY_REQUIRED_ID
from reportmachine_runner.intent_contract import EMPTY_SCOPE_DELTA_FINGERPRINT
from reportmachine_runner.phase_contracts import build_run_artifacts
from reportmachine_runner.phase_provenance import (
    PROVENANCE_ORDER,
    ProvenanceContext,
    rendered_images_available,
    write_ingest_receipt,
    write_phase_checkpoint,
)
from reportmachine_runner.run_outputs import IMAGE_LEDGER_HEADER
from reportmachine_runner.source_ingest import SourceIngestResult, build_source_ingest_paths
from reportmachine_runner.source_ledgers import (
    RENDERED_IMAGES_HEADER,
    SOURCE_LEDGER_CANDIDATE_HEADER,
)
from reportmachine_runner.summary import (
    SUMMARY_CLAIM_MAP_HEADER,
    SUMMARY_SCHEMA,
    build_summary_run_config,
    source_hashes,
)

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:
    Draft202012Validator = None


# Schemas the runner-driven phase pipeline validates against. Temp test roots need all of
# them because each phase validates its artifacts before the next phase may start.
PIPELINE_SCHEMAS = (
    "schemas/article_result.schema.json",
    "schemas/intent_contract.schema.json",
    "schemas/research_question_tree.schema.json",
    "schemas/source_candidate.schema.json",
    "schemas/evidence_card.schema.json",
    "schemas/contradiction_ledger.schema.json",
    "schemas/evidence_gap_map.schema.json",
    "schemas/thesis_decision.schema.json",
    "schemas/narrative_plan.schema.json",
    "schemas/citation_logic_report.schema.json",
    "schemas/red_team_report.schema.json",
    "schemas/qa_report.schema.json",
    "schemas/compression_report.schema.json",
    "schemas/image_audit_report.schema.json",
    "schemas/revision_notes.schema.json",
    "schemas/paragraph_map.schema.json",
    "schemas/final_sync_report.schema.json",
    "schemas/agent_attempt.schema.json",
)


class CliHarness(unittest.TestCase):
    # Most CLI tests here run the default `all` phase and stub out Codex
    # execution, so they never reach the finalize-phase schema validation that
    # needs the real dependency. They may therefore bypass the early gate when
    # the dependency is absent. Tests asserting gate behavior opt out.
    bypass_schema_dependency_gate = True

    # The runner now executes ordered role phases. Legacy tests here stub a single
    # Codex invocation and assert finalize-stage behavior (command construction,
    # output validation, latest mirroring, artifact export). For those, the harness
    # targets `--phase finalize` against seeded upstream artifacts. Tests that
    # exercise phase sequencing or gates opt out and pass their own `--phase`.
    auto_finalize_phase = True

    # Seeded artifacts represent phases a prior run already completed, so by default they
    # also get the runner's provenance checkpoints. Tests that assert the legacy
    # (pre-provenance) resume behavior opt out.
    seed_phase_provenance = True

    def resolve_argv(self, argv: list[str]) -> list[str]:
        if not self.auto_finalize_phase:
            return argv
        opt_outs = {"--phase", "--prepare-only", "--dry-run", "--smoke-test"}
        if any(item in opt_outs for item in argv):
            return argv
        return [*argv, "--phase", "finalize", "--intake-approve-scope"]

    def prepare_project_root(self, argv: list[str]) -> None:
        """Ensure a pipeline-capable project root for tests that build bare temp dirs.

        Several tests create `temp/project-root` directly instead of calling
        `init_git_root`, so they lack the schemas and upstream artifacts the phase
        pipeline validates. Seed them here rather than editing each test.
        """

        if not self.auto_finalize_phase or "--project-root" not in argv:
            return
        # Only seed when this invocation will actually execute phases. Smoke and dry-run
        # tests assert that nothing is created on disk.
        if "--phase" not in argv:
            return
        root = Path(argv[argv.index("--project-root") + 1])
        root.mkdir(parents=True, exist_ok=True)
        for rel in (
            "schemas/claim_ledger.schema.json",
            "schemas/earnings_delta.schema.json",
            *PIPELINE_SCHEMAS,
        ):
            if not (root / rel).is_file():
                self.copy_schema(root, rel)
        run_id = argv[argv.index("--run-id") + 1] if "--run-id" in argv else "test_run"
        mode = argv[argv.index("--mode") + 1] if "--mode" in argv else "industry"
        if not (root / "work" / "runs" / run_id / "intake" / "intent_contract.json").is_file():
            self.seed_pipeline_artifacts(root, run_id=run_id, mode=mode)

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = self.resolve_argv(argv)
        self.prepare_project_root(argv)
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            state_temp = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(
                patch(
                    "reportmachine_runner.execution_control.default_state_root",
                    return_value=Path(state_temp) / "state",
                )
            )
            if (
                self.bypass_schema_dependency_gate
                and importlib.util.find_spec("jsonschema") is None
            ):
                stack.enter_context(
                    patch.object(
                        workflow_module,
                        "schema_validation_dependency_error",
                        return_value=None,
                    )
                )
            try:
                status = cli.main(argv)
            except SystemExit as exc:
                status = exc.code if isinstance(exc.code, int) else 1
        return status, stdout.getvalue() + stderr.getvalue()

    def init_git_root(self, temp_dir: str) -> Path:
        root = Path(temp_dir) / "project-root"
        root.mkdir(parents=True)
        subprocess.run(
            ["git", "init"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.copy_schema(root, "schemas/claim_ledger.schema.json")
        self.copy_schema(root, "schemas/earnings_delta.schema.json")
        for rel in PIPELINE_SCHEMAS:
            self.copy_schema(root, rel)
        if self.auto_finalize_phase:
            # Finalize gates on upstream phase artifacts. Seed schema-valid ones so
            # legacy tests can keep asserting finalize-stage behavior directly.
            self.seed_pipeline_artifacts(root)
        return root

    def approve_scope_args(self) -> list[str]:
        """CLI flags that satisfy the scope gate for unattended test runs."""

        return ["--intake-approve-scope"]

    def article_sha256(self, text: str) -> str:
        from hashlib import sha256

        return sha256(text.encode("utf-8")).hexdigest()

    def write_default_outputs(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        missing: set[str] | None = None,
    ) -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "article_result.json",
            "article.md",
            "source_ledger.csv",
            "image_ledger.csv",
            "claim_ledger.json",
        ]:
            if missing and name in missing:
                continue
            if name == "claim_ledger.json":
                content = "[]\n"
            elif name == "source_ledger.csv":
                content = (
                    "source_id,url,local_path,sha256,ingest_status,allowed_domain,blocked_reason\n"
                )
            elif name == "image_ledger.csv":
                content = (
                    "image_path,keep_or_drop,visual_question,supported_claim_ids,"
                    "why_prose_is_insufficient\n"
                )
            elif name == "article_result.json":
                # Must stay schema-shaped: the finalize audit reads article_sha256 from it.
                content = json.dumps(
                    {
                        "article_type": "industry",
                        "article_sha256": self.article_sha256("article.md"),
                        "title": "제목",
                        "subtitle": "부제",
                        "one_sentence_insight": "핵심",
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
                        "checklist_status": "pass",
                    },
                    ensure_ascii=False,
                )
                content += "\n"
            else:
                content = name
            (output_dir / name).write_text(content, encoding="utf-8")
        # The finalize audit re-derives the article hash and requires the appendix and
        # paragraph map, so write those unless the test is deliberately omitting outputs.
        self.write_finalize_side_artifacts(root, run_id=run_id, missing=missing)

    def write_finalize_side_artifacts(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        missing: set[str] | None = None,
    ) -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        article_path = output_dir / "article.md"
        if not article_path.is_file():
            return
        digest = self.article_sha256(article_path.read_text(encoding="utf-8"))
        (output_dir / "research_appendix.md").write_text("# 연구 부록\n", encoding="utf-8")
        self.write_json(
            output_dir / "paragraph_map.json",
            {
                "version": 1,
                "run_id": run_id,
                "article_sha256": digest,
                "paragraphs": [],
            },
        )
        result_path = output_dir / "article_result.json"
        if missing and "article_result.json" in missing:
            return
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(payload, dict):
            payload["article_sha256"] = digest
            self.write_json(result_path, payload)

    def write_finalize_side_artifacts_for(
        self,
        root: Path,
        *,
        run_id: str,
        article_rel: str,
        result_rel: str,
    ) -> None:
        """Write appendix, paragraph map, and article hash for custom output paths."""

        digest = self.write_appendix_and_paragraph_map(
            root, run_id=run_id, article_rel=article_rel
        )
        result_path = root / result_rel
        self.write_json(
            result_path,
            {
                "article_type": "industry",
                "article_sha256": digest,
                "title": "제목",
                "subtitle": "부제",
                "one_sentence_insight": "핵심",
                "scorecard": None,
                "output_files": {
                    "article_md": article_rel,
                    "source_ledger_csv": f"outputs/{run_id}/source_ledger.csv",
                    "image_ledger_csv": f"outputs/{run_id}/image_ledger.csv",
                    "claim_ledger_json": f"outputs/{run_id}/claim_ledger.json",
                    "preflight_result_json": f"outputs/{run_id}/preflight_result.json",
                    "run_manifest_json": f"outputs/{run_id}/run_manifest.json",
                },
                "critical_warnings": [],
                "checklist_status": "pass",
            },
        )

    def write_appendix_and_paragraph_map(
        self,
        root: Path,
        *,
        run_id: str,
        article_rel: str,
    ) -> str:
        """Write the finalize-owned appendix and paragraph map; return the article hash."""

        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        article_path = root / article_rel
        digest = self.article_sha256(
            article_path.read_text(encoding="utf-8") if article_path.is_file() else ""
        )
        (output_dir / "research_appendix.md").write_text("# 연구 부록\n", encoding="utf-8")
        self.write_json(
            output_dir / "paragraph_map.json",
            {
                "version": 1,
                "run_id": run_id,
                "article_sha256": digest,
                "paragraphs": [],
            },
        )
        return digest

    def write_valid_article_outputs(self, root: Path, *, run_id: str = "test_run") -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        article_text = "# 제목\n\n본문입니다.\n"
        (output_dir / "article.md").write_text(article_text, encoding="utf-8")
        (output_dir / "source_ledger.csv").write_text(
            "source_id,url,local_path,sha256,ingest_status,allowed_domain,blocked_reason\n"
            "s1,https://example.com,inputs/source.md,abc,registered_local,example.com,\n",
            encoding="utf-8",
        )
        (output_dir / "image_ledger.csv").write_text(
            "image_path,keep_or_drop,visual_question,supported_claim_ids,"
            "why_prose_is_insufficient\n"
            ",drop,,,\n",
            encoding="utf-8",
        )
        (output_dir / "claim_ledger.json").write_text("[]\n", encoding="utf-8")
        (output_dir / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "industry",
                    "article_sha256": self.article_sha256(article_text),
                    "title": "제목",
                    "subtitle": "부제",
                    "one_sentence_insight": "핵심",
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
                    "checklist_status": "pass",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_finalize_side_artifacts(root, run_id=run_id)

    def copy_schema(self, root: Path, rel: str) -> None:
        source = Path(__file__).resolve().parents[1] / rel
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def seed_pipeline_artifacts(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        mode: str = "industry",
        include_images: bool = True,
    ) -> None:
        """Write schema-valid upstream phase artifacts for an approved run.

        Tests that exercise finalize, export, mirroring, or audit behavior use this so
        they can run `--phase finalize` without simulating every research role.
        """

        work = root / "work" / "runs" / run_id
        for name in ["intake", "research", "research/lanes", "evidence", "drafts", "reviews"]:
            (work / name).mkdir(parents=True, exist_ok=True)

        # A real run has its declared inputs before any phase executes, so create them first.
        # Seeding them after the checkpoints would look like inputs appearing out of nowhere.
        self.ensure_run_inputs(root)

        (work / "intake" / "original_request.md").write_text(
            "원본 사용자 요청입니다.\n", encoding="utf-8"
        )
        self.write_json(
            work / "intake" / "intent_contract.json",
            {
                "version": 1,
                "run_id": run_id,
                "article_type": mode,
                "original_request": "원본 사용자 요청입니다.",
                "primary_question": "핵심 질문입니다.",
                "must_include": [],
                "must_not_include": ["target price"],
                "proposed_exclusions": [],
                "scope_delta": [],
                "scope_status": "aligned",
                "analysis_modes": ["industry_structure"],
                "depth": "standard",
                "approval_status": "approved",
                "approved_at": "2026-07-29T00:00:00Z",
                # Fingerprint of an empty material scope delta, matching the runner's
                # own value for a contract approved with no material change.
                "approved_scope_delta_sha256": EMPTY_SCOPE_DELTA_FINGERPRINT,
                "base_date": "2026-07-29",
                "required_user_decisions": [],
                "warnings": [],
            },
        )
        self.write_json(
            work / "research" / "research_question_tree.json",
            {
                "version": 1,
                "run_id": run_id,
                "central_question": "핵심 질문입니다.",
                "questions": [
                    {
                        "question_id": "Q1",
                        "question": "구조가 바뀌고 있는가",
                        "importance": "p0",
                        "hypotheses": ["바뀐다", "바뀌지 않는다"],
                        "confirming_evidence_needed": ["공식 자료"],
                        "disconfirming_evidence_needed": ["반대 공식 자료"],
                        "stop_conditions": ["직접 근거 확보"],
                    }
                ],
                "lanes": [
                    {"lane_id": "structure", "title": "구조", "question_ids": ["Q1"]}
                ],
            },
        )
        (work / "research" / "source_candidates.primary.jsonl").write_text("", encoding="utf-8")
        (work / "research" / "source_candidates.counter.jsonl").write_text("", encoding="utf-8")
        (work / "evidence" / "evidence_cards.jsonl").write_text(
            json.dumps(
                {
                    "evidence_id": "E001",
                    "claim_ids": ["C001"],
                    "evidence_class": "unknown",
                    "source_id": "S001",
                    "locator": {"page": None, "section": None, "url_fragment": None},
                    "supports": ["테스트 주장"],
                    "does_not_establish": ["추가 사실"],
                    "independence": "unknown",
                    "confidence": "low",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.write_json(
            work / "research" / "contradiction_ledger.json",
            {"version": 1, "run_id": run_id, "contradictions": []},
        )
        self.write_json(
            work / "research" / "evidence_gap_map.json",
            {
                "version": 1,
                "run_id": run_id,
                "extra_research_recommended": False,
                "gaps": [],
            },
        )
        self.write_json(
            work / "research" / "thesis_decision.json",
            {
                "version": 1,
                "run_id": run_id,
                "hypotheses": [
                    {
                        "hypothesis_id": f"H{index}",
                        "statement": f"가설 {index}",
                        "user_fit": "high",
                        "evidence_coverage": "medium",
                        "falsifiability": "반증 조건이 있습니다",
                        "explanatory_power": "medium",
                        "overreach_risk": "low",
                    }
                    for index in (1, 2, 3)
                ],
                "chosen_hypothesis_id": "H1",
                "selection_rationale": "근거와 한계를 함께 설명합니다.",
                "falsification_conditions": ["반대 근거가 나오면 철회합니다"],
                "central_sentence": "중심 문장입니다.",
            },
        )
        self.write_json(
            work / "research" / "narrative_plan.json",
            {
                "version": 1,
                "run_id": run_id,
                "central_sentence": "중심 문장입니다.",
                "sections": [
                    {
                        "section_id": "S1",
                        "working_heading": "제목",
                        "new_claim_or_causal_link": "새 인과 연결",
                        "claim_ids": ["C001"],
                        "target": "article",
                    }
                ],
            },
        )
        (work / "drafts" / "draft_article.md").write_text(
            "# 제목\n\n본문입니다.\n<!-- claims: C001 | evidence: E001 -->\n",
            encoding="utf-8",
        )
        self.write_json(
            work / "reviews" / "citation_logic.json",
            {"version": 1, "run_id": run_id, "overall_status": "pass", "issues": []},
        )
        self.write_json(
            work / "reviews" / "red_team.json",
            {
                "version": 1,
                "run_id": run_id,
                "strongest_counter_thesis": "반대 논지입니다.",
                "alternative_causes": ["다른 원인"],
                "single_source_that_would_flip": "결정적 자료",
                "verdict": "thesis_durable",
                "issues": [],
            },
        )
        self.write_json(
            work / "reviews" / "qa_report.json",
            {"version": 1, "run_id": run_id, "overall_status": "pass", "issues": []},
        )
        self.write_json(
            work / "reviews" / "compression.json",
            {
                "version": 1,
                "run_id": run_id,
                "issues": [],
                "duplicate_claim_clusters": [],
                "appendix_candidates": [],
            },
        )
        if include_images:
            self.write_json(
                work / "reviews" / "image_audit.json",
                {"version": 1, "run_id": run_id, "issues": [], "images": []},
            )
        self.write_json(
            work / "reviews" / "revision_notes.json",
            {
                "version": 1,
                "run_id": run_id,
                "handled_issues": [],
                "unresolved_blocking_issues": [],
            },
        )
        if self.seed_phase_provenance:
            self.seed_provenance_checkpoints(
                root,
                run_id=run_id,
                mode=mode,
                include_images=include_images,
            )

    def provenance_context(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        mode: str = "industry",
        include_images: bool = True,
    ) -> ProvenanceContext:
        artifacts = build_run_artifacts(
            work_run_dir=f"work/runs/{run_id}",
            outputs_dir=f"outputs/{run_id}",
            article=f"outputs/{run_id}/article.md",
            source_ledger=f"outputs/{run_id}/source_ledger.csv",
            image_ledger=f"outputs/{run_id}/image_ledger.csv",
            claim_ledger=f"outputs/{run_id}/claim_ledger.json",
            article_result=f"outputs/{run_id}/article_result.json",
        )
        earnings: tuple[str, ...] = ()
        if mode == "earnings":
            earnings = (
                f"outputs/{run_id}/earnings_delta_matrix.json",
                f"outputs/{run_id}/earnings_delta_matrix.md",
            )
        ingest_paths = build_source_ingest_paths(f"work/runs/{run_id}")
        return ProvenanceContext(
            root=root,
            run_id=run_id,
            artifacts=artifacts,
            # Resolve images exactly as the runner does, so a seeded checkpoint always
            # describes the same artifact set the run will validate against.
            include_images=rendered_images_available(
                root,
                ingest_paths.rendered_images_ledger,
                fallback=include_images,
            ),
            earnings_delta=earnings,
            # Match the CLI defaults so a seeded checkpoint tracks the same declared inputs
            # the run will validate against.
            topic_path="inputs/topic.md",
            sources_path="inputs/sources.csv",
            run_manifest=f"outputs/{run_id}/run_manifest.json",
        )

    def seed_provenance_checkpoints(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        mode: str = "industry",
        include_images: bool = True,
        through: str | None = None,
    ) -> None:
        """Record completion checkpoints for the artifacts just seeded.

        Seeded artifacts stand in for phases a real run already completed, so they need the
        matching provenance the runner would have written. Using the production writer keeps
        the fixture honest: a checkpoint format change cannot pass here and fail in a run.
        """

        context = self.provenance_context(
            root,
            run_id=run_id,
            mode=mode,
            include_images=include_images,
        )
        last = through or "revise"
        for phase in PROVENANCE_ORDER[: PROVENANCE_ORDER.index(last) + 1]:
            write_phase_checkpoint(context, phase)

    def reseed_provenance_if_present(self, root: Path, *, run_id: str = "test_run") -> None:
        """Refresh seeded checkpoints after a fixture adds run inputs.

        `init_git_root` seeds artifacts and their checkpoints, but some tests then create
        `inputs/` files the run reads. A real run has those inputs before any phase runs, so
        re-record the checkpoints rather than leaving them describing a run without inputs.
        """

        provenance = root / "work" / "runs" / run_id / "provenance"
        if not provenance.is_dir():
            return
        recorded = sorted(
            path.stem for path in provenance.glob("*.json") if path.stem in PROVENANCE_ORDER
        )
        if not recorded:
            return
        last = max(recorded, key=PROVENANCE_ORDER.index)
        self.seed_provenance_checkpoints(root, run_id=run_id, through=last)

    def seed_ingest_receipt(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        mode: str = "industry",
        stage: str = "evidence",
        input_paths: tuple[str, ...] = ("inputs/sources.csv",),
    ) -> None:
        """Record the receipt the runner writes after a successful source ingest.

        The pre-ingest state is treated as an empty ledger, which models "the runner ingested
        every row currently present". Input hashes come from the files as they stand now, the
        same way a real ingest records them.
        """

        context = self.provenance_context(root, run_id=run_id, mode=mode)
        write_ingest_receipt(
            context,
            stage,
            before_rows={},
            input_paths=input_paths,
        )

    def ensure_run_inputs(self, root: Path) -> None:
        """Create the run inputs a real run always has before any phase executes."""

        topic = root / "inputs" / "topic.md"
        sources = root / "inputs" / "sources.csv"
        topic.parent.mkdir(parents=True, exist_ok=True)
        if not topic.is_file():
            topic.write_text("주제 입력입니다.\n", encoding="utf-8")
        if not sources.is_file():
            sources.write_text("source_id,url\n", encoding="utf-8")

    def enable_rendered_image(self, root: Path, *, run_id: str = "test_run") -> str:
        """Give the run one inspectable rendered image, as source ingest would.

        `has_rendered_images()` decides whether the image audit is under contract, and it
        answers from the rendered image ledger plus the file it points at. Both are needed for
        a fixture to model an image-bearing run.
        """

        paths = build_source_ingest_paths(f"work/runs/{run_id}")
        image_rel = f"work/runs/{run_id}/images/rendered/deck_page_001.png"
        image_path = root / image_rel
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_bytes = b"\x89PNG\r\n\x1a\n"
        image_path.write_bytes(image_bytes)
        from hashlib import sha256

        image_hash = sha256(image_bytes).hexdigest()
        ledger = root / paths.rendered_images_ledger
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=RENDERED_IMAGES_HEADER)
            writer.writeheader()
            writer.writerow(
                {
                    "image_path": image_rel,
                    "source_doc": "inputs/deck.pdf",
                    "source_id": "deck",
                    "page": "1",
                    "source_hash": "deckhash",
                    "image_hash": image_hash,
                    "render_dpi": "144",
                    "status": "ok",
                    "reason": "",
                }
            )
        self.write_json(
            root / f"work/runs/{run_id}/reviews/image_audit.json",
            {
                "version": 1,
                "run_id": run_id,
                "issues": [],
                "images": [
                    {
                        "image_path": image_rel,
                        "source_doc": "inputs/deck.pdf",
                        "source_id": "deck",
                        "page_or_slide_number": 1,
                        "source_hash": "deckhash",
                        "visual_question": "",
                        "supported_claim_ids": [],
                        "why_prose_is_insufficient": "",
                        "actual_slide_title": "",
                        "detected_key_datapoints": [],
                        "caption_candidate": "",
                        "article_anchor_before": "",
                        "article_anchor_after": "",
                        "keep_or_drop": "drop",
                        "drop_reason": "fixture has no article image requirement",
                    }
                ],
            },
        )
        return image_rel

    def write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )

    def write_finalize_outputs(self, root: Path, *, run_id: str = "test_run") -> None:
        """Write final outputs that satisfy the code-level cross audit."""

        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        article_text = "# 제목\n\n본문입니다.\n"
        (output_dir / "article.md").write_text(article_text, encoding="utf-8")
        (output_dir / "research_appendix.md").write_text("# 연구 부록\n", encoding="utf-8")
        digest = self.article_sha256(article_text)
        self.write_json(
            output_dir / "paragraph_map.json",
            {
                "version": 1,
                "run_id": run_id,
                "article_sha256": digest,
                "paragraphs": [
                    {
                        "paragraph_id": "P001",
                        "heading": "제목",
                        "line_start": 3,
                        "line_end": 3,
                        "claim_ids": ["C001"],
                    }
                ],
            },
        )
        (output_dir / "source_ledger.csv").write_text(
            "source_id,url,local_path,sha256,ingest_status,allowed_domain,blocked_reason\n"
            "s1,https://example.com,inputs/source.md,abc,registered_local,example.com,\n",
            encoding="utf-8",
        )
        report_path = root / f"work/runs/{run_id}/reviews/image_audit.json"
        rendered_path = root / build_source_ingest_paths(
            f"work/runs/{run_id}"
        ).rendered_images_ledger
        try:
            report_payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report_payload = {}
        report_images = (
            report_payload.get("images", [])
            if isinstance(report_payload, dict)
            else []
        )
        rendered_by_path: dict[str, dict[str, str]] = {}
        if rendered_path.is_file():
            with rendered_path.open(newline="", encoding="utf-8") as handle:
                rendered_by_path = {
                    str(row.get("image_path") or ""): row
                    for row in csv.DictReader(handle)
                    if str(row.get("image_path") or "")
                }
        image_rows: list[dict[str, object]] = []
        for report_row in report_images if isinstance(report_images, list) else []:
            if not isinstance(report_row, dict):
                continue
            image_rel = str(report_row.get("image_path") or "")
            rendered_row = rendered_by_path.get(image_rel, {})
            image_rows.append(
                {
                    **report_row,
                    "page_or_slide_number": str(
                        report_row.get("page_or_slide_number") or ""
                    ),
                    "supported_claim_ids": json.dumps(
                        report_row.get("supported_claim_ids") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "detected_key_datapoints": json.dumps(
                        report_row.get("detected_key_datapoints") or [],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    "image_hash": rendered_row.get("image_hash", ""),
                    "inspected_status": rendered_row.get("status", ""),
                    "caption_basis": "canonical image audit",
                    "caption": str(report_row.get("caption_candidate") or ""),
                }
            )
        if not image_rows:
            image_rows = [{"keep_or_drop": "drop"}]
        with (output_dir / "image_ledger.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=IMAGE_LEDGER_HEADER)
            writer.writeheader()
            writer.writerows(image_rows)
        self.write_json(output_dir / "claim_ledger.json", [])
        self.write_json(
            output_dir / "article_result.json",
            {
                "article_type": "industry",
                "article_sha256": digest,
                "title": "제목",
                "subtitle": "부제",
                "one_sentence_insight": "핵심",
                "scorecard": None,
                "output_files": {
                    "article_md": f"outputs/{run_id}/article.md",
                    "source_ledger_csv": f"outputs/{run_id}/source_ledger.csv",
                    "image_ledger_csv": f"outputs/{run_id}/image_ledger.csv",
                    "claim_ledger_json": f"outputs/{run_id}/claim_ledger.json",
                    "preflight_result_json": f"outputs/{run_id}/preflight_result.json",
                    "run_manifest_json": f"outputs/{run_id}/run_manifest.json",
                    "research_appendix_md": f"outputs/{run_id}/research_appendix.md",
                    "paragraph_map_json": f"outputs/{run_id}/paragraph_map.json",
                },
                "critical_warnings": [],
                "checklist_status": "pass",
            },
        )

    def minimal_earnings_delta_payload(self) -> dict[str, object]:
        return {
            "delta_table": [],
            "disappeared_highlights": [],
            "wording_shifts": [],
            "guidance_assumption_changes": [],
            "ir_frame": "",
            "counter_reading": "",
            "scorecard_candidate": {
                "total": 0,
                "headline": 0,
                "quality": 0,
                "leading_indicators": 0,
                "transparency": 0,
                "execution": 0,
                "summary": "No delta rows.",
            },
            "next_quarter_verification_points": [],
        }

    def write_valid_earnings_outputs(
        self,
        root: Path,
        *,
        run_id: str = "test_run",
        delta_payload: object | str | None = None,
        write_delta: bool = True,
    ) -> None:
        output_dir = root / "outputs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "article.md").write_text("## 한눈에 보기\n\n본문입니다.\n", encoding="utf-8")
        (output_dir / "source_ledger.csv").write_text(
            "source_id,url,local_path,sha256,ingest_status,allowed_domain,blocked_reason\n"
            "src_001,https://example.com,inputs/source.md,abc,registered_local,example.com,\n",
            encoding="utf-8",
        )
        (output_dir / "image_ledger.csv").write_text(
            "image_path,keep_or_drop,visual_question,supported_claim_ids,why_prose_is_insufficient\n"
            ",drop,,,\n",
            encoding="utf-8",
        )
        (output_dir / "claim_ledger.json").write_text("[]\n", encoding="utf-8")
        scorecard = {
            "total": 0,
            "headline": 0,
            "quality": 0,
            "leading_indicators": 0,
            "transparency": 0,
            "execution": 0,
            "summary": "No scorecard rows.",
        }
        (output_dir / "article_result.json").write_text(
            json.dumps(
                {
                    "article_type": "earnings",
                    "article_sha256": self.article_sha256("## 한눈에 보기\n\n본문입니다.\n"),
                    "title": "제목",
                    "subtitle": "부제",
                    "one_sentence_insight": "핵심",
                    "scorecard": scorecard,
                    "output_files": {
                        "article_md": f"outputs/{run_id}/article.md",
                        "source_ledger_csv": f"outputs/{run_id}/source_ledger.csv",
                        "image_ledger_csv": f"outputs/{run_id}/image_ledger.csv",
                        "claim_ledger_json": f"outputs/{run_id}/claim_ledger.json",
                        "preflight_result_json": f"outputs/{run_id}/preflight_result.json",
                        "run_manifest_json": f"outputs/{run_id}/run_manifest.json",
                        "earnings_delta_matrix_json": f"outputs/{run_id}/earnings_delta_matrix.json",
                        "earnings_delta_matrix_md": f"outputs/{run_id}/earnings_delta_matrix.md",
                    },
                    "critical_warnings": [],
                    "checklist_status": "pass",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if write_delta:
            if delta_payload is None:
                delta_payload = self.minimal_earnings_delta_payload()
            delta_text = (
                delta_payload
                if isinstance(delta_payload, str)
                else json.dumps(delta_payload, ensure_ascii=False)
            )
            (output_dir / "earnings_delta_matrix.json").write_text(
                delta_text + "\n", encoding="utf-8"
            )
            (output_dir / "earnings_delta_matrix.md").write_text(
                "# Earnings delta\n", encoding="utf-8"
            )
        self.write_finalize_side_artifacts(root, run_id=run_id)

    def write_config(self, root: Path, text: str, name: str = "reportmachine.toml") -> Path:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_complete_earnings_sources(self, root: Path) -> None:
        input_dir = root / "inputs"
        source_dir = root / "fixtures"
        input_dir.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            ("cur_release", "current", "earnings_release"),
            ("cur_deck", "current", "investor_presentation"),
            ("prev_release", "previous", "earnings_release"),
            ("prev_deck", "previous", "investor_presentation"),
        ]
        for source_id, _period_role, _doc_kind in rows:
            (source_dir / f"{source_id}.txt").write_text(source_id, encoding="utf-8")
        with (input_dir / "sources.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            for source_id, period_role, doc_kind in rows:
                writer.writerow(
                    {
                        "source_id": source_id,
                        "article_type": "earnings",
                        "company": "Example",
                        "period_role": period_role,
                        "period": "Q1",
                        "doc_kind": doc_kind,
                        "issuer": "Example",
                        "date": "2026-01-01",
                        "title": source_id,
                        "url": "",
                        "source_tier": "primary",
                        "required": "true",
                        "local_path": f"fixtures/{source_id}.txt",
                        "notes": "",
                    }
                )
        # A real run has its inputs before any phase runs, so refresh seeded checkpoints
        # rather than leaving them describing a run that had no source inventory.
        self.reseed_provenance_if_present(root)

    def write_summary_schema(self, root: Path) -> None:
        schema_source = Path(__file__).resolve().parents[1] / SUMMARY_SCHEMA
        target = root / SUMMARY_SCHEMA
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(schema_source.read_text(encoding="utf-8"), encoding="utf-8")

    def write_claim_map_with_row(self, root: Path, config) -> None:
        claim_map = root / config.paths.summary_claim_map
        claim_map.parent.mkdir(parents=True, exist_ok=True)
        with claim_map.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_CLAIM_MAP_HEADER)
            writer.writeheader()
            writer.writerow(
                {
                    "summary_item_id": "s1",
                    "summary_text": "본문입니다.",
                    "claim_type": "statement",
                    "source_artifact": "article.md",
                    "source_path": config.sources.article,
                    "source_anchor": "# 제목",
                    "source_url": "",
                    "notes": "test row",
                }
            )

    def write_success_summary_result(
        self,
        root: Path,
        run_id: str = "test_run",
        *,
        hashes: dict[str, str] | None = None,
    ) -> None:
        config = build_summary_run_config(root=root, run_id=run_id)
        hashes = hashes or source_hashes(config)
        summary_dir = root / config.paths.summary_dir
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / "summary_a4.png").write_bytes(b"png")
        self.write_claim_map_with_row(root, config)
        (summary_dir / "summary_result.json").write_text(
            json.dumps(
                {
                    "summary_type": "a4_visual",
                    "source_article": config.sources.article,
                    "source_article_sha256": hashes["article_sha256"],
                    "source_result": config.sources.article_result,
                    "source_result_sha256": hashes["article_result_sha256"],
                    "image_generation_surface": "codex_internal_tool",
                    "output_files": {
                        "summary_a4_png": config.paths.summary_image,
                        "summary_prompt_md": config.paths.summary_prompt,
                        "summary_claim_map_csv": config.paths.summary_claim_map,
                        "summary_manifest_json": config.paths.summary_manifest,
                    },
                    "critical_warnings": [],
                    "checklist_status": "pass",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def assert_no_ingest_artifacts_in_prompt(self, prompt: str) -> None:
        self.assertNotIn("source_ingest_manifest.json", prompt)
        self.assertNotIn("source_ledger.candidate.csv", prompt)
        self.assertNotIn("pdf_index.jsonl", prompt)
        self.assertNotIn("When source preprocessing is enabled", prompt)
        self.assertNotIn("Use preprocessed source candidates", prompt)
        self.assertNotIn("Use PDF search chunks", prompt)
