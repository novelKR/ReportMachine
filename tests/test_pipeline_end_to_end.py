"""End-to-end pipeline test with a scripted fake Codex.

Runs `--phase all` against a stub that writes each phase's artifacts, proving the ordered
role sequence, per-phase validation, attempt envelopes, and the final cross audit work
together rather than only in isolation.
"""

from __future__ import annotations

from tests.cli_harness import *


class PipelineEndToEndTests(CliHarness):
    auto_finalize_phase = False

    def test_full_phase_sequence_produces_auditable_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self.init_git_root(temp_dir)
            for rel in PIPELINE_SCHEMAS:
                self.copy_schema(root, rel)
            source = root / "inputs" / "source.txt"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("고객 인증 절차가 진행 중입니다.", encoding="utf-8")
            (root / "inputs" / "sources.csv").write_text(
                "source_id,local_path\nS001,inputs/source.txt\n",
                encoding="utf-8",
            )
            calls: list[str] = []

            def fake_run(command, *, cwd: Path, **_kwargs) -> int:
                # The compiled prompt is the final argument; identify the phase from it.
                prompt = command[-1]
                phase = self.phase_from_prompt(prompt)
                calls.append(phase)
                self.write_phase_artifacts(root, phase)
                return 0

            with patch("reportmachine_runner.workflow.run_command", side_effect=fake_run):
                status, output = self.run_cli(
                    [
                        "--mode",
                        "industry",
                        "--run-id",
                        "e2e_run",
                        "--project-root",
                        str(root),
                        "--intake-original-request",
                        "삼성전자와 하이닉스 흐름까지 고려한 판단을 원합니다",
                        "--intake-approve-scope",
                        "--intake-depth",
                        "brief",
                        "--intake-analysis-mode",
                        "market_reaction",
                        "--intake-analysis-mode",
                        "company_fundamental",
                    ]
                )

            self.assertEqual(status, 0, output)

            # Roles ran in pipeline order, each as its own Codex call.
            self.assertEqual(
                calls,
                [
                    "scope",
                    "research-plan",
                    "research-lane",
                    "research-counter",
                    "evidence-verify",
                    "evidence-integrate",
                    "thesis",
                    "narrative",
                    "draft",
                    "citation",
                    "redteam",
                    "qa",
                    "compression",
                    "revise",
                    "finalize",
                ],
                output,
            )

            run_dir = root / "work" / "runs" / "e2e_run"
            outputs = root / "outputs" / "e2e_run"

            # The original request and approved scope are preserved for audit.
            self.assertIn(
                "삼성전자와 하이닉스",
                (run_dir / "intake" / "original_request.md").read_text(encoding="utf-8"),
            )
            intent = json.loads(
                (run_dir / "intake" / "intent_contract.json").read_text(encoding="utf-8")
            )
            self.assertEqual(intent["approval_status"], "approved")
            self.assertEqual(intent["depth"], "brief")
            self.assertIn("market_reaction", intent["analysis_modes"])

            # Every role left an execution envelope and a compiled prompt.
            attempts = sorted((run_dir / "agents").rglob("attempt_*.json"))
            self.assertEqual(len(attempts), len(calls))
            roles = {
                json.loads(path.read_text(encoding="utf-8"))["agent_name"] for path in attempts
            }
            for expected in (
                "scope_guardian",
                "research_planner",
                "primary_source_scout",
                "counterevidence_scout",
                "evidence_verifier",
                "thesis_adjudicator",
                "article_writer",
                "editorial_red_team",
                "finalizer",
            ):
                self.assertIn(expected, roles)
            self.assertTrue(list((run_dir / "contracts").glob("effective_prompt_*.md")))

            # Research candidates were promoted through the runner-owned second ingest.
            primary_candidate = json.loads(
                (run_dir / "research" / "source_candidates.primary.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertEqual(primary_candidate["source_id"], "CAND-1")
            self.assertEqual(primary_candidate["ingest_status"], "remote_unverified")
            ingest_manifest = json.loads(
                (run_dir / "research" / "source_ingest_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(ingest_manifest["counts"]["research_sources_added"], 2)
            research_receipt = json.loads(
                (run_dir / "provenance" / "ingest" / "research.json").read_text(
                    encoding="utf-8"
                )
            )
            source_ledger_rel = (
                "work/runs/e2e_run/ledgers/source_ledger.candidate.csv"
            )
            self.assertEqual(
                set(research_receipt["ledger_row_hashes"][source_ledger_rel]),
                {"S001", "CAND-1", "CAND-2"},
            )

            # The final cross audit ran and passed.
            report = json.loads(
                (outputs / "final_sync_report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(report["status"], "pass", report["errors"])
            self.assertTrue(report["regenerated_after_freeze"])

            manifest = json.loads((outputs / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["agent_executions"]), len(calls))

            # The independently invoked audit re-runs schema and cross-artifact checks without Codex.
            audit_status, audit_output = self.run_cli(
                [
                    "--mode",
                    "industry",
                    "--phase",
                    "audit",
                    "--run-id",
                    "e2e_run",
                    "--project-root",
                    str(root),
                    "--no-source-ingest",
                ]
            )
            self.assertEqual(audit_status, 0, audit_output)
            self.assertIn("audit_status: pass", audit_output)

    def phase_from_prompt(self, prompt: str) -> str:
        markers = [
            ("Compare the recorded original request", "scope"),
            ("Design the research question tree", "research-plan"),
            ("You are assigned lane", "research-lane"),
            ("would falsify this run's hypotheses", "research-counter"),
            ("Turn source candidates into verifiable evidence cards", "evidence-verify"),
            ("Merge duplicates, record conflicts", "evidence-integrate"),
            ("Compare at least three competing theses", "thesis"),
            ("Turn the chosen thesis into a section plan", "narrative"),
            ("Write the draft article from approved artifacts", "draft"),
            ("Audit the draft's factual and logical integrity", "citation"),
            ("Attack the draft's thesis", "redteam"),
            ("Review the draft against every applicable contract", "qa"),
            ("Find repeated conclusions", "compression"),
            ("Revise the draft against every review report", "revise"),
            ("Freeze the article", "finalize"),
        ]
        for marker, phase in markers:
            if marker in prompt:
                return phase
        return "unknown"

    def write_phase_artifacts(self, root: Path, phase: str) -> None:
        run_dir = root / "work" / "runs" / "e2e_run"
        if phase == "scope":
            existing = json.loads(
                (run_dir / "intake" / "intent_contract.json").read_text(encoding="utf-8")
            )
            existing["scope_status"] = "aligned"
            self.write_json(run_dir / "intake" / "intent_contract.json", existing)
        elif phase == "research-plan":
            self.write_json(
                run_dir / "research" / "research_question_tree.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "central_question": "방어력이 유지되는가",
                    "questions": [
                        {
                            "question_id": "Q1",
                            "question": "고객 인증이 진행되는가",
                            "importance": "p0",
                            "hypotheses": ["진행된다", "지연된다"],
                            "confirming_evidence_needed": ["고객 발표"],
                            "disconfirming_evidence_needed": ["샘플 단계 자료"],
                            "stop_conditions": ["직접 근거 확보"],
                        }
                    ],
                    "lanes": [
                        {"lane_id": "qualification", "title": "고객 인증", "question_ids": ["Q1"]}
                    ],
                },
            )
        elif phase == "research-lane":
            self.write_jsonl(
                run_dir / "research" / "lanes" / "lane_qualification.primary.jsonl",
                [
                    {
                        "candidate_id": "CAND-1",
                        "lane_id": "qualification",
                        "question_ids": ["Q1"],
                        "search_intent": "confirming",
                        "title": "공식 발표",
                        "why_relevant": "인증 단계를 직접 언급합니다",
                    }
                ],
            )
        elif phase == "research-counter":
            self.write_jsonl(
                run_dir / "research" / "lanes" / "lane_counter.counter.jsonl",
                [
                    {
                        "candidate_id": "CAND-2",
                        "question_ids": ["Q1"],
                        "search_intent": "disconfirming",
                        "title": "지연 사례",
                        "why_relevant": "반대 가설을 지지합니다",
                    }
                ],
            )
        elif phase == "evidence-verify":
            self.write_jsonl(
                run_dir / "evidence" / "evidence_cards.jsonl",
                [
                    {
                        "evidence_id": "E001",
                        "claim_ids": ["C001"],
                        "evidence_class": "issuer_claim",
                        "source_id": "S001",
                        "exact_quote": "고객 인증 절차가 진행 중입니다",
                        "locator": {"page": None, "section": "본문", "url_fragment": None},
                        "supports": ["인증 진행"],
                        "does_not_establish": ["인증 완료"],
                        "independence": "non_independent",
                        "confidence": "medium",
                    }
                ],
            )
        elif phase == "evidence-integrate":
            self.write_json(
                run_dir / "research" / "contradiction_ledger.json",
                {"version": 1, "run_id": "e2e_run", "contradictions": []},
            )
            self.write_json(
                run_dir / "research" / "evidence_gap_map.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "extra_research_recommended": False,
                    "gaps": [],
                },
            )
        elif phase == "thesis":
            self.write_json(
                run_dir / "research" / "thesis_decision.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "hypotheses": [
                        {
                            "hypothesis_id": f"H{index}",
                            "statement": f"가설 {index}",
                            "user_fit": "high",
                            "evidence_coverage": "medium",
                            "falsifiability": "반증 조건 있음",
                            "explanatory_power": "medium",
                            "overreach_risk": "low",
                        }
                        for index in (1, 2, 3)
                    ],
                    "chosen_hypothesis_id": "H1",
                    "selection_rationale": "근거와 한계를 함께 설명합니다",
                    "falsification_conditions": ["반대 근거 등장"],
                    "central_sentence": "인증이 방어선을 결정합니다.",
                },
            )
        elif phase == "narrative":
            self.write_json(
                run_dir / "research" / "narrative_plan.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "central_sentence": "인증이 방어선을 결정합니다.",
                    "sections": [
                        {
                            "section_id": "S1",
                            "working_heading": "고객 인증이 방어선입니다",
                            "new_claim_or_causal_link": "인증 지연이 위협을 늦춥니다",
                            "claim_ids": ["C001"],
                            "target": "article",
                        }
                    ],
                },
            )
        elif phase in {"draft", "revise"}:
            (run_dir / "drafts").mkdir(parents=True, exist_ok=True)
            (run_dir / "drafts" / "draft_article.md").write_text(
                "# 고객 인증이 방어선입니다\n\n본문입니다.\n<!-- claims: C001 | evidence: E001 -->\n",
                encoding="utf-8",
            )
            if phase == "revise":
                self.write_json(
                    run_dir / "reviews" / "revision_notes.json",
                    {
                        "version": 1,
                        "run_id": "e2e_run",
                        "handled_issues": [
                            {
                                "source_report": "red_team",
                                "issue_id": "RT-1",
                                "resolution": "fixed",
                                "note": "논지의 조건을 본문에 명시했습니다.",
                            }
                        ],
                        "unresolved_blocking_issues": [],
                    },
                )
        elif phase == "citation":
            self.write_json(
                run_dir / "reviews" / "citation_logic.json",
                {"version": 1, "run_id": "e2e_run", "overall_status": "pass", "issues": []},
            )
        elif phase == "redteam":
            self.write_json(
                run_dir / "reviews" / "red_team.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "strongest_counter_thesis": "반대 논지",
                    "alternative_causes": ["다른 원인"],
                    "single_source_that_would_flip": "결정적 자료",
                    "verdict": "thesis_needs_qualification",
                    "issues": [
                        {
                            "issue_id": "RT-1",
                            "priority": "blocking",
                            "issue": "인증 진행과 완료를 구분해야 합니다.",
                            "required_fix": "논지를 인증 진행 단계로 한정합니다.",
                        }
                    ],
                },
            )
        elif phase == "qa":
            self.write_json(
                run_dir / "reviews" / "qa_report.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "overall_status": "pass",
                    "issues": [],
                },
            )
        elif phase == "compression":
            self.write_json(
                run_dir / "reviews" / "compression.json",
                {
                    "version": 1,
                    "run_id": "e2e_run",
                    "issues": [],
                    "duplicate_claim_clusters": [],
                    "appendix_candidates": [],
                },
            )
        elif phase == "finalize":
            self.write_finalized_outputs(root)

    def write_finalized_outputs(self, root: Path) -> None:
        outputs = root / "outputs" / "e2e_run"
        outputs.mkdir(parents=True, exist_ok=True)
        article = "# 고객 인증이 방어선입니다\n\n본문입니다.\n"
        (outputs / "article.md").write_text(article, encoding="utf-8")
        (outputs / "research_appendix.md").write_text("# 연구 부록\n", encoding="utf-8")
        digest = self.article_sha256(article)
        self.write_json(
            outputs / "paragraph_map.json",
            {
                "version": 1,
                "run_id": "e2e_run",
                "article_sha256": digest,
                "paragraphs": [
                    {
                        "paragraph_id": "P001",
                        "heading": "고객 인증이 방어선입니다",
                        "line_start": 3,
                        "line_end": 3,
                        "claim_ids": ["C001"],
                        "evidence_ids": ["E001"],
                    }
                ],
            },
        )
        (outputs / "source_ledger.csv").write_text(
            "source_id,url,local_path,sha256,ingest_status,allowed_domain,blocked_reason\n"
            "S001,https://example.com,inputs/source.md,abc,registered_local,example.com,\n",
            encoding="utf-8",
        )
        (outputs / "image_ledger.csv").write_text(
            "image_path,keep_or_drop,visual_question,supported_claim_ids,why_prose_is_insufficient\n"
            ",drop,,,\n",
            encoding="utf-8",
        )
        self.write_json(
            outputs / "claim_ledger.json",
            [
                {
                    "claim_id": "C001",
                    "claim_text": "고객 인증 절차가 진행 중입니다.",
                    "claim_type": "structural_fact",
                    "article_section": "고객 인증이 방어선입니다",
                    "article_anchor": {
                        "heading": "고객 인증이 방어선입니다",
                        "paragraph_id": "P001",
                    },
                    "evidence_ids": ["E001"],
                    "source_ids": ["S001"],
                    "source_quote_or_datapoint": "고객 인증 절차가 진행 중입니다",
                    "confidence": "medium",
                    "support_status": "conditionally_supported",
                    "needs_human_review": False,
                    "reason": "회사 자료만으로는 완료를 확인할 수 없습니다.",
                }
            ],
        )
        self.write_json(
            outputs / "article_result.json",
            {
                "article_type": "industry",
                "article_sha256": digest,
                "analysis_modes": ["market_reaction", "company_fundamental"],
                "title": "고객 인증이 방어선입니다",
                "subtitle": "무엇이 방어력을 결정하는가",
                "one_sentence_insight": "인증이 방어선을 결정합니다.",
                "scorecard": None,
                "output_files": {
                    "article_md": "outputs/e2e_run/article.md",
                    "source_ledger_csv": "outputs/e2e_run/source_ledger.csv",
                    "image_ledger_csv": "outputs/e2e_run/image_ledger.csv",
                    "claim_ledger_json": "outputs/e2e_run/claim_ledger.json",
                    "preflight_result_json": "outputs/e2e_run/preflight_result.json",
                    "run_manifest_json": "outputs/e2e_run/run_manifest.json",
                    "research_appendix_md": "outputs/e2e_run/research_appendix.md",
                    "paragraph_map_json": "outputs/e2e_run/paragraph_map.json",
                    "final_sync_report_json": "outputs/e2e_run/final_sync_report.json",
                },
                "critical_warnings": [],
                "checklist_status": "pass",
            },
        )


if __name__ == "__main__":
    unittest.main()
