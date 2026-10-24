"""Unit tests for the validator, policy and agent failure paths. No API calls:
the agent is driven by a scripted fake client.

    python -m unittest discover -s code/tests -v
"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic                                           # noqa: E402
import httpx2                                              # noqa: E402

import main                                                # noqa: E402
from deletion_audit import agent as agent_mod              # noqa: E402
from deletion_audit.agent import AgentJudge                # noqa: E402
from deletion_audit.data import load_dataset               # noqa: E402
from deletion_audit.evidence_index import build_index      # noqa: E402
from deletion_audit.gather import gather                   # noqa: E402
from deletion_audit.judge_baseline import judge_baseline   # noqa: E402
from deletion_audit.pipeline import finalize, run_case     # noqa: E402
from deletion_audit.policy import Fact, Judgment, treatment  # noqa: E402
from deletion_audit.steps import build_steps               # noqa: E402
from deletion_audit.tools import CaseTools                 # noqa: E402
from deletion_audit.validate import validate_row           # noqa: E402

DS = load_dataset()
INDEX = build_index()


# ---------------------------------------------------------------- fake client
def _usage():
    return SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def text_response(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, usage=_usage())


def tool_response(*calls, stop="tool_use"):
    blocks = [SimpleNamespace(type="tool_use", name=n, input=i, id=f"toolu_{k}") for k, (n, i) in enumerate(calls)]
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=_usage())


INTENT_DELETE = text_response(json.dumps({"authorizes_deletion": True, "request_type": "deletion",
                                          "language": "en", "rationale": "test"}))


class FakeClient:
    """Replays scripted responses; once the script runs out, repeats `default`."""

    def __init__(self, script, default=None):
        self.script, self.default, self.calls = list(script), default, []
        self.messages = self

    def create(self, **kwargs):
        # snapshot: the agent keeps appending to the same messages list
        self.calls.append({**kwargs, "messages": copy.deepcopy(kwargs.get("messages"))})
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, BaseException):
            raise item
        return item


def fact(rid, category, state, evidence, system="", condition_met=True):
    return {"resource_id": rid, "system_id": system or INDEX.system_of(rid) or "x", "category": category,
            "state": state, "evidence_id": evidence, "condition_met": condition_met, "reason": "test"}


def submit(*facts):
    return tool_response(("submit_facts", {"facts": list(facts), "rejected": [], "summary": "test"}))


def judge(case_id, script, default=None, max_turns=16):
    req = DS.request(case_id)
    client = FakeClient(script, default)
    j, transcript = AgentJudge(client=client, max_turns=max_turns).judge(req, gather(req, index=INDEX), INDEX)
    return req, j, client


# ---------------------------------------------------------------- validator
class ValidatorTests(unittest.TestCase):
    def setUp(self):
        facts = [Fact("search_052", "search_service", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"),
                 Fact("account_4248", "account_service", "USER_PROFILE", "ABSENT", "evidence_03")]
        self.status, self.steps, _ = build_steps(facts)

    def test_built_steps_are_valid(self):
        self.assertEqual(self.status, "INCOMPLETE")
        self.assertEqual(validate_row(self.status, self.steps, {"evidence_03"}), [])

    def test_out_of_order(self):
        steps = [dict(s) for s in self.steps]
        steps[0], steps[1] = steps[1], steps[0]
        for i, s in enumerate(steps, 1):
            s["step_no"] = i
        self.assertTrue(any("out of order" in e for e in validate_row(self.status, steps)))

    def test_wrong_state_and_unknown_operation(self):
        steps = [dict(s) for s in self.steps]
        steps[0]["state"] = "ABSENT"
        steps[1]["operation"] = "PURGE_EVERYTHING"
        errs = validate_row(self.status, steps)
        self.assertTrue(any("must have state" in e for e in errs))
        self.assertTrue(any("unknown operation" in e for e in errs))

    def test_future_step_with_evidence_and_finding_without(self):
        steps = [dict(s) for s in self.steps]
        steps[0]["evidence_id"] = ""
        steps[-1]["evidence_id"] = "evidence_03"
        errs = validate_row(self.status, steps)
        self.assertTrue(any("missing evidence_id" in e for e in errs))
        self.assertTrue(any("future step must have empty evidence_id" in e for e in errs))

    def test_unusable_evidence(self):
        errs = validate_row(self.status, self.steps, usable_evidence={"evidence_01"})
        self.assertTrue(any("not usable at audit_at" in e for e in errs))

    def test_status_shape_rules(self):
        self.assertTrue(validate_row("NOT_APPLICABLE", self.steps))
        self.assertTrue(any("only contain VERIFICATION" in e for e in validate_row("COMPLETE", self.steps)))
        self.assertTrue(validate_row("MAYBE", []))

    def test_step_numbering_and_keys(self):
        steps = [dict(s) for s in self.steps]
        steps[2]["step_no"] = 7
        steps[3]["extra"] = 1
        errs = validate_row(self.status, steps)
        self.assertTrue(any("step_no 7" in e for e in errs))
        self.assertTrue(any("keys" in e for e in errs))


# ---------------------------------------------------------------- policy
class PolicyTests(unittest.TestCase):
    def test_expired_retention_is_deleted(self):
        self.assertEqual(treatment("SECURITY_AUDIT_LOG", "PRESENT", condition_met=True)[2], "VERIFY_RECORD_PRESENT")
        self.assertEqual(treatment("SECURITY_AUDIT_LOG", "PRESENT", condition_met=False),
                         ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"))

    def test_shared_attachment_is_redacted(self):
        self.assertEqual(treatment("SUPPORT_ATTACHMENT", "PRESENT", condition_met=False)[1], "REDACT_RECORD")

    def test_tombstone(self):
        self.assertEqual(treatment("ACCOUNT_TOMBSTONE", "TREATED"), (None, None, "VERIFY_RECORD_REDACTED"))
        self.assertEqual(treatment("ACCOUNT_TOMBSTONE", "PRESENT")[0], "REPORT_EXPOSED_PII")

    def test_cutoff_exception_never_changes_status(self):
        # INCOMPLETE dev case with a post-audit-only retained record: verification added, status kept
        req = DS.request("case_23")
        j = Judgment("case_23", "test", True, facts=[
            Fact("search_898", "search_service", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_92")])
        res = finalize(req, j)
        self.assertEqual(res.status, "INCOMPLETE")
        added = [s for s in res.steps if s["resource_id"] == "audit_record_4128"]
        self.assertEqual([(s["operation"], s["evidence_id"]) for s in added], [("VERIFY_RECORD_PRESENT", "")])
        # COMPLETE case: never touched
        j2 = Judgment("case_23", "test", True, facts=[
            Fact("account_410", "account_service", "USER_PROFILE", "ABSENT", "evidence_92")])
        res2 = finalize(req, j2)
        self.assertEqual(res2.status, "COMPLETE")
        self.assertFalse(any(s["resource_id"] == "audit_record_4128" for s in res2.steps))


# ---------------------------------------------------------------- agent failure paths
class AgentFailurePathTests(unittest.TestCase):
    def test_hallucinated_resource_is_dropped(self):
        _, j, _ = judge("case_11", [INTENT_DELETE, submit(
            fact("search_052", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"),
            fact("search_999999", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"))])
        self.assertEqual([f.resource_id for f in j.facts], ["search_052"])
        self.assertTrue(any("search_999999" in n and "out-of-episode" in n for n in j.notes))

    def test_hallucinated_evidence_is_replaced_with_usable_record(self):
        req, j, _ = judge("case_11", [INTENT_DELETE, submit(
            fact("search_052", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_999"))])
        usable = CaseTools(req, INDEX).usable_evidence_ids()
        self.assertIn(j.facts[0].evidence_id, usable)
        self.assertNotEqual(j.facts[0].evidence_id, "evidence_999")

    def test_post_audit_evidence_is_replaced(self):
        # evidence_02 was captured 2026-09-17, after case_12's audit_at (2026-09-15T12:00Z)
        req, j, _ = judge("case_12", [INTENT_DELETE, submit(
            fact("analytics_9914", "ANALYTICS_PROFILE", "PRESENT", "evidence_02"))])
        self.assertNotEqual(j.facts[0].evidence_id, "evidence_02")
        self.assertIn(j.facts[0].evidence_id, CaseTools(req, INDEX).usable_evidence_ids())
        res = finalize(req, j)
        self.assertEqual(res.errors, [])

    def test_tools_never_reveal_post_audit_evidence(self):
        tools = CaseTools(DS.request("case_12"), INDEX)
        self.assertIn("error", tools.read_evidence_file("evidence_02"))
        self.assertIn("error", tools.query_sql_snapshot("evidence_52", None))  # captured 09-20
        hits = tools.search_evidence("audit_record_7095")["records"]
        self.assertTrue(all(h["evidence_id"] != "evidence_02" for h in hits))

    def test_malformed_facts_are_dropped(self):
        _, j, _ = judge("case_11", [INTENT_DELETE, submit(
            fact("search_052", "NOT_A_CATEGORY", "PRESENT", "evidence_03"),
            fact("search_052", "SEARCH_INDEX_ENTRY", "MAYBE", "evidence_03"),
            fact("DROP TABLE x", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"),
            fact("support_864", "SUPPORT_MESSAGE", "PRESENT", "evidence_03"))])
        self.assertEqual([f.resource_id for f in j.facts], ["support_864"])
        self.assertEqual(sum("malformed" in n for n in j.notes), 3)

    def test_bad_tool_calls_return_errors_and_loop_continues(self):
        _, j, client = judge("case_11", [
            INTENT_DELETE,
            tool_response(("no_such_tool", {}), ("query_sql_snapshot", {"file_id": "evidence_03", "sql": "x"}),
                          ("get_log_trace", {"file_id": "evidence_01"})),  # missing arg -> TypeError
            submit(fact("search_052", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"))])
        results = client.calls[2]["messages"][-1]["content"]
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.get("is_error") for r in results))
        self.assertEqual([f.resource_id for f in j.facts], ["search_052"])

    def test_iteration_cap_falls_back_to_baseline(self):
        req = DS.request("case_11")
        search = tool_response(("search_evidence", {"query": "user_32", "limit": 1}))
        _, j, client = judge("case_11", [INTENT_DELETE], default=search, max_turns=3)
        self.assertEqual(len(client.calls), 1 + 3)
        last_user = client.calls[-1]["messages"][-1]["content"]
        self.assertTrue(any(b.get("type") == "text" and "Turn limit" in b.get("text", "") for b in last_user))
        self.assertTrue(any("did not submit" in n for n in j.notes))
        baseline = judge_baseline(req, gather(req, index=INDEX), INDEX)
        self.assertEqual([(f.resource_id, f.state) for f in j.facts],
                         [(f.resource_id, f.state) for f in baseline.facts])

    def test_text_only_turn_is_nudged(self):
        _, j, client = judge("case_11", [INTENT_DELETE, text_response("I think I am done."),
                                         submit(fact("search_052", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"))])
        self.assertIn("submit_facts", client.calls[2]["messages"][-1]["content"])
        self.assertEqual(len(j.facts), 1)

    def test_max_tokens_stop_falls_back(self):
        _, j, _ = judge("case_11", [INTENT_DELETE, text_response("…", stop="max_tokens")])
        self.assertTrue(any("max_tokens" in n for n in j.notes))
        self.assertTrue(any("did not submit" in n for n in j.notes))

    def test_not_deletion_intent_exits_without_investigation(self):
        no = text_response(json.dumps({"authorizes_deletion": False, "request_type": "correction",
                                       "language": "en", "rationale": "test"}))
        req = DS.request("case_11")
        client = FakeClient([no])
        res = run_case(req, "agent", AgentJudge(client=client))
        self.assertEqual((res.status, res.steps), ("NOT_APPLICABLE", []))
        self.assertEqual(len(client.calls), 1)

    def test_connection_errors_are_retried(self):
        err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.invalid"))
        with mock.patch.object(agent_mod.time, "sleep"):
            _, j, client = judge("case_11", [INTENT_DELETE, err, err,
                                             submit(fact("search_052", "SEARCH_INDEX_ENTRY", "PRESENT", "evidence_03"))])
        self.assertEqual(len(j.facts), 1)

    def test_persistent_api_error_raises(self):
        err = RuntimeError("HTTP 529 overloaded")
        with self.assertRaises(RuntimeError):
            judge("case_11", [INTENT_DELETE, err])


# ---------------------------------------------------------------- checkpoints
class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(main, "RUNS_DIR", Path(self.tmp.name))
        self.patch.start()
        self.reqs = [DS.request("case_05"), DS.request("case_11")]

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_failed_case_stays_pending_and_no_output(self):
        real = main.run_case

        def flaky(req, mode, agent=None):
            if req["case_id"] == "case_11":
                raise RuntimeError("API down")
            return real(req, mode, agent)

        with mock.patch.object(main, "run_case", flaky), self.assertRaises(SystemExit):
            main.run_cases(self.reqs, "baseline", 2, "t")
        d = main.checkpoint_dir("baseline", "t")
        self.assertTrue((d / "case_05.json").exists())
        self.assertFalse((d / "case_11.json").exists())
        meta = json.loads((d / "run_meta.json").read_text())
        self.assertIn("case_11", meta["failed"])
        # resume: only the failed case is judged again
        calls = []
        with mock.patch.object(main, "run_case", lambda r, m, a=None: calls.append(r["case_id"]) or real(r, m, a)):
            main.run_cases(self.reqs, "baseline", 1, "t")
        self.assertEqual(calls, ["case_11"])

    def test_checkpoints_from_another_version_are_never_reused(self):
        main.run_cases(self.reqs, "baseline", 1, "t")
        calls = []
        real = main.run_case
        with mock.patch.object(main, "judge_version", lambda mode: "otherversion"), \
                mock.patch.object(main, "run_case", lambda r, m, a=None: calls.append(r["case_id"]) or real(r, m, a)):
            main.run_cases(self.reqs, "baseline", 1, "t")
        self.assertEqual(sorted(calls), ["case_05", "case_11"])

    def test_tampered_version_field_is_not_reused(self):
        main.run_cases(self.reqs, "baseline", 1, "t")
        path = main.checkpoint_dir("baseline", "t") / "case_05.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["judge_version"] = "stale"
        path.write_text(json.dumps(saved), encoding="utf-8")
        calls = []
        real = main.run_case
        with mock.patch.object(main, "run_case", lambda r, m, a=None: calls.append(r["case_id"]) or real(r, m, a)):
            main.run_cases(self.reqs, "baseline", 1, "t")
        self.assertEqual(calls, ["case_05"])

    def test_version_changes_with_prompt_text(self):
        v1 = main.judge_version("agent")
        from deletion_audit import prompts
        path = Path(prompts.__file__)
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"\n# version probe\n")
            self.assertNotEqual(main.judge_version("agent"), v1)
        finally:
            path.write_bytes(original)
        self.assertEqual(main.judge_version("agent"), v1)


if __name__ == "__main__":
    unittest.main()
