"""Regression test against the committed SanDisk run.

That run passed the old audit while its claim-ledger anchors had drifted from the final
article, three claims rested on blocked sources, and no article hash was recorded. The new
cross audit must fail it. Skips when the run artifacts are not present in the checkout.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from reportmachine_runner.final_sync import run_final_sync_audit
from reportmachine_runner.phase_contracts import build_run_artifacts


RUN_ID = "sandisk_nand_china_20260729"
REPO_ROOT = Path(__file__).resolve().parents[1]


def run_artifacts():
    return build_run_artifacts(
        work_run_dir=f"work/runs/{RUN_ID}",
        outputs_dir=f"outputs/{RUN_ID}",
        article=f"outputs/{RUN_ID}/article.md",
        source_ledger=f"outputs/{RUN_ID}/source_ledger.csv",
        image_ledger=f"outputs/{RUN_ID}/image_ledger.csv",
        claim_ledger=f"outputs/{RUN_ID}/claim_ledger.json",
        article_result=f"outputs/{RUN_ID}/article_result.json",
    )


@unittest.skipUnless(
    (REPO_ROOT / "outputs" / RUN_ID / "article.md").is_file(),
    f"{RUN_ID} outputs are not present in this checkout",
)
class RetroSanDiskAuditTests(unittest.TestCase):
    def audit(self):
        return run_final_sync_audit(
            root=REPO_ROOT,
            run_id=RUN_ID,
            artifacts=run_artifacts(),
            require_evidence_cards=False,
        )

    def test_previously_passing_run_now_fails(self) -> None:
        result = self.audit()
        self.assertFalse(result.passed, "the legacy run should not pass the new cross audit")
        self.assertEqual(result.payload["status"], "fail")

    def test_stale_claim_anchors_are_reported(self) -> None:
        errors = "\n".join(self.audit().errors)
        self.assertIn("claim_anchors_exist_in_frozen_article", errors)
        # C004's recorded section never existed in the published article.
        self.assertIn("최근 계기: BiCS10과 K2가 동시에 움직였습니다", errors)

    def test_blocked_source_claims_are_reported(self) -> None:
        errors = "\n".join(self.audit().errors)
        for claim_id, source_id in (("C017", "S016"), ("C018", "S017"), ("C020", "S019")):
            self.assertIn(claim_id, errors)
            self.assertIn(source_id, errors)

    def test_missing_article_hash_is_reported(self) -> None:
        errors = "\n".join(self.audit().errors)
        self.assertIn("does not record article_sha256", errors)

    def test_report_payload_is_schema_shaped(self) -> None:
        payload = self.audit().payload
        for key in (
            "version",
            "run_id",
            "generated_at",
            "article_sha256",
            "regenerated_after_freeze",
            "status",
            "checks",
            "errors",
            "warnings",
        ):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["article_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
