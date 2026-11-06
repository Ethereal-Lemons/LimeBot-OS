from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from core.task_eval import (
    EvaluationPlan,
    Expectations,
    FileCheck,
    PlannedToolCall,
    ReplyCheck,
    ReplayTurn,
    SourceSnapshot,
    TaskCase,
    evaluate,
    validate_plan,
)


ROOT = Path(__file__).resolve().parents[1]


class TestTaskEvaluation(unittest.TestCase):
    def test_offline_plan_rejects_network_tools(self):
        case = TaskCase(
            id="network-refused",
            prompt="Search the web.",
            replay=(ReplayTurn(tool_calls=(PlannedToolCall("web_search", {"query": "x"}),)),),
        )
        plan = EvaluationPlan(
            cases=(case,),
            baseline=SourceSnapshot("baseline", ROOT),
            candidate=SourceSnapshot("candidate", ROOT),
        )
        with self.assertRaisesRegex(ValueError, "not allowed"):
            validate_plan(plan)

        tagged_case = TaskCase(
            id="network-tag-refused",
            prompt="Deliver a message.",
            replay=(ReplayTurn(content="<discord_send>secret</discord_send>"),),
        )
        tagged_plan = EvaluationPlan(
            cases=(tagged_case,),
            baseline=SourceSnapshot("baseline", ROOT),
            candidate=SourceSnapshot("candidate", ROOT),
        )
        with self.assertRaisesRegex(ValueError, "delivery tag"):
            validate_plan(tagged_plan)

    def test_paired_report_is_repeatable_redacted_and_isolated(self):
        unique = uuid.uuid4().hex
        filename = f"workspace/eval-{unique}.txt"
        source_path = ROOT / filename
        self.assertFalse(source_path.exists())
        case = TaskCase(
            id="redacted-write",
            prompt=f"Write the private fixture token {unique} to the evaluation note, verify it, and report {unique}.",
            replay=(
                ReplayTurn(
                    tool_calls=(
                        PlannedToolCall(
                            "write_file",
                            {
                                "path": filename,
                                "content": f"FIXTURE_SECRET_{unique}\n",
                            },
                        ),
                    )
                ),
                ReplayTurn(
                    tool_calls=(
                        PlannedToolCall(
                            "verify_files", {"paths": [filename]}
                        ),
                    )
                ),
                ReplayTurn(content=f"The evaluation note contains {unique}."),
            ),
            approvals=("write_file",),
            expectations=Expectations(
                status="completed",
                verification="passed",
                required_tools=("write_file", "verify_files"),
                reply_checks=(ReplyCheck("reply-token", unique),),
                file_checks=(
                    FileCheck(
                        "fixture-written",
                        filename,
                        equals=f"FIXTURE_SECRET_{unique}\n",
                    ),
                ),
            ),
        )
        plan = EvaluationPlan(
            cases=(case,),
            baseline=SourceSnapshot("baseline", ROOT),
            candidate=SourceSnapshot("candidate", ROOT),
            repeats=2,
            timeout_seconds=45,
        )

        with tempfile.TemporaryDirectory(prefix="limebot-task-eval-test-") as temp:
            state_dir = Path(temp) / "state"
            with patch.dict(os.environ, {"LIMEBOT_STATE_DIR": str(state_dir)}):
                report = asyncio.run(evaluate(plan))

            self.assertEqual(len(report.runs), 4)
            self.assertEqual(report.aggregate["paired_runs"], 2)
            self.assertEqual(report.aggregate["candidate_passes"], 2)
            self.assertEqual(report.recommendation, "task_path_passed")
            self.assertFalse(source_path.exists())
            report_path = state_dir / "data" / "evaluations" / f"{report.report_id}.json"
            persisted = report_path.read_text(encoding="utf-8")
            self.assertNotIn(unique, persisted)
            self.assertNotIn("FIXTURE_SECRET", persisted)
            report_data = json.loads(persisted)
            self.assertEqual(report_data["aggregate"]["baseline_runs"], 2)
            self.assertEqual(report_data["aggregate"]["candidate_runs"], 2)


if __name__ == "__main__":
    unittest.main()
