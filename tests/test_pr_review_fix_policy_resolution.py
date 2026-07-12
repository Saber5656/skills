import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (ROOT / "pr-review-fix-policy" / "SKILL.md").read_text(encoding="utf-8")
README_TEXT = (ROOT / "pr-review-fix-policy" / "README.md").read_text(encoding="utf-8")
EVALS = json.loads(
    (ROOT / "pr-review-fix-policy" / "evals" / "evals.json").read_text(encoding="utf-8")
)
NORMALIZED_SKILL = " ".join(SKILL_TEXT.split())


class PrReviewFixPolicyResolutionTests(unittest.TestCase):
    def test_policy_phase_remains_read_only(self) -> None:
        self.assertIn("このスキルではGitHubへ返信しない", SKILL_TEXT)
        self.assertIn("このスキルではreview threadをresolveしない", SKILL_TEXT)
        self.assertIn("ユーザーが実装を承認したら", SKILL_TEXT)

    def test_code_change_operations_have_safe_order(self) -> None:
        self.assertIn(
            "implement → validate → commit → push → verify remote head → refresh thread state → reply → refresh thread state → resolve → verify isResolved",
            NORMALIZED_SKILL,
        )

    def test_explanation_only_does_not_require_empty_commit(self) -> None:
        self.assertIn(
            "validate explanation → mark commit/push/remote-head not_applicable → refresh thread state → reply → refresh thread state → resolve → verify isResolved",
            NORMALIZED_SKILL,
        )
        self.assertIn("コード変更がない場合に空commitや不要なpushを作らない", SKILL_TEXT)
        self.assertIn("do not create an empty commit", SKILL_TEXT)

    def test_resolution_requires_reply_and_remote_success(self) -> None:
        self.assertIn("コード変更がある場合はfix commitのremote-head確認前にreply/resolveしない", SKILL_TEXT)
        self.assertIn("thread返信が失敗 | そのthreadはresolveせず", SKILL_TEXT)
        self.assertIn("完了扱いしない", SKILL_TEXT)

    def test_thread_state_is_refreshed_before_each_mutation(self) -> None:
        self.assertIn("reply直前とresolve直前にthread-aware stateを再取得", SKILL_TEXT)
        self.assertIn("GraphQL thread node ID、承認scope", SKILL_TEXT)
        self.assertIn("確認失敗またはscope/state変化時は次のmutationを行わない", SKILL_TEXT)

    def test_excluded_and_outdated_threads_keep_fetched_state(self) -> None:
        self.assertIn("取得時の状態を変更しない", SKILL_TEXT)
        self.assertNotIn("outdated threadはopenのまま", SKILL_TEXT)
        self.assertIn("resolve_status: not_applicable", SKILL_TEXT)

    def test_option_a_explicitly_authorizes_reply_and_resolution(self) -> None:
        self.assertIn(
            "including per-thread replies, resolution after each successful reply",
            SKILL_TEXT,
        )
        self.assertIn(
            "approval of this handoff explicitly authorizes per-thread replies and resolution",
            SKILL_TEXT,
        )

    def test_top_level_comments_are_not_resolved(self) -> None:
        combined = f"{SKILL_TEXT}\n{README_TEXT}"
        self.assertIn("top-level PR comments", combined)
        self.assertIn("resolve_status: not_applicable", combined)
        self.assertIn("review-thread resolve mutation", combined)

    def test_completion_evidence_is_reported_per_item(self) -> None:
        self.assertIn("Required completion evidence per item", SKILL_TEXT)
        self.assertIn("reply status and URL when available", SKILL_TEXT)
        self.assertIn("verified `isResolved`/`isOutdated` value or `not_applicable`", SKILL_TEXT)

    def test_resolution_edge_cases_have_objective_evals(self) -> None:
        expected_markers = {
            9: "Resolves each thread only after its reply succeeds",
            10: "Resolves each addressed thread individually after its own reply succeeds",
            11: "Does not resolve a review thread whose reply failed",
            12: "Classifies top-level PR comment resolution as not_applicable",
            13: "Does not create an empty commit or require a new push for explanation-only work",
            14: "Keeps the thread unresolved when resolve mutation or verification fails",
            15: "Does not reply to or resolve a thread that became outdated",
            16: "Does not invoke a duplicate resolve mutation when isResolved is already true",
        }
        for eval_id, marker in expected_markers.items():
            case = next(item for item in EVALS["evals"] if item["id"] == eval_id)
            self.assertIn(marker, case["expectations"])


if __name__ == "__main__":
    unittest.main()
