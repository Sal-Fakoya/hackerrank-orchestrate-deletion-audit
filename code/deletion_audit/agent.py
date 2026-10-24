"""Agent-mode judge: Claude Haiku 5.5 with tools over the evidence index.

1. Intent: one structured-output call. Not a deletion request -> stop.
2. Investigation: a manual tool-use loop seeded with the deterministic candidate
   list. The model decides when it is done by calling submit_facts; a hard
   turn cap forces a submission, and if none arrives the case falls back to
   the baseline judgment.
3. Python checks every submitted fact (category/state vocabulary, evidence
   usable at audit_at) before the step builder sees it.
"""

import json
import re
import time

import anthropic
from dotenv import load_dotenv

from .data import load_dataset, parse_ts
from .evidence_index import EvidenceIndex
from .gather import Gathered, other_requests
from .identity import resolver
from .judge_baseline import judge_baseline, latest_observation
from .paths import ENV_FILE
from .policy import STATES, Fact, Judgment, categories
from .prompts import AGENT_SYSTEM, INTENT_SCHEMA, INTENT_SYSTEM, case_message
from .tools import TOOL_DEFS, CaseTools, dumps

MODEL = "claude-haiku-5-5"
MAX_TURNS = 16
MAX_TOKENS = 16000
EFFORT = "medium"
RESOURCE_RE = re.compile(r"^[a-z_]+_\d+$")


class AgentJudge:
    def __init__(self, model: str = MODEL, max_turns: int = MAX_TURNS, effort: str = EFFORT, client=None):
        if client is None:
            load_dotenv(ENV_FILE)
            client = anthropic.Anthropic(max_retries=6)
        self.client = client  # injectable for tests
        self.model, self.max_turns, self.effort = model, max_turns, effort

    # ---------------------------------------------------------------- usage
    def _usage(self, case_id: str, call: str, response) -> dict:
        u = response.usage
        return {
            "case_id": case_id, "call": call, "model": self.model,
            "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        }

    # ---------------------------------------------------------------- intent
    def classify_intent(self, request: dict, usage: list) -> dict:
        response = self.client.messages.create(
            model=self.model, max_tokens=2000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": INTENT_SCHEMA}},
            system=INTENT_SYSTEM,
            messages=[{"role": "user", "content":
                       f"<request_text>\n{request['request_text']}\n</request_text>"}],
        )
        usage.append(self._usage(request["case_id"], "intent", response))
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    # ---------------------------------------------------------------- judge
    def judge(self, request: dict, g: Gathered, index: EvidenceIndex) -> tuple[Judgment, list]:
        case_id = request["case_id"]
        usage: list[dict] = []
        transcript: list[dict] = []
        intent = self.classify_intent(request, usage)
        j = Judgment(case_id, "agent", bool(intent["authorizes_deletion"]), usage=usage)
        j.notes.append(f"intent: {intent['request_type']} ({intent['language']}): {intent['rationale']}")
        if not j.deletion_requested:
            return j, transcript

        tools = CaseTools(request, index)
        messages = [{"role": "user", "content": self._case_prompt(request, g, index, tools)}]
        submission = None
        for turn in range(1, self.max_turns + 1):
            if turn == self.max_turns and isinstance(messages[-1]["content"], list):
                messages[-1]["content"].append(
                    {"type": "text", "text": "Turn limit reached: call submit_facts now with what you have."})
            response = self._create(messages)
            usage.append(self._usage(case_id, f"turn_{turn}", response))
            messages.append({"role": "assistant", "content": response.content})
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason in ("max_tokens", "refusal"):
                j.notes.append(f"turn {turn}: stop_reason {response.stop_reason}")
                break
            if not calls:
                messages.append({"role": "user", "content": "Call submit_facts to finish this case."})
                continue
            results = []
            for call in calls:
                if call.name == "submit_facts":
                    submission = call.input
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": "submitted"})
                    continue
                try:
                    out = tools.call(call.name, call.input)
                except Exception as e:  # tool bug or bad argument: tell the model, keep going
                    out = {"error": f"{type(e).__name__}: {e}"}
                transcript.append({"turn": turn, "tool": call.name, "input": call.input,
                                   "output_chars": len(dumps(out))})
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": dumps(out),
                                **({"is_error": True} if isinstance(out, dict) and "error" in out else {})})
            messages.append({"role": "user", "content": results})
            if submission is not None:
                break

        if submission is None:
            fallback = judge_baseline(request, g, index)
            j.facts, j.notes = fallback.facts, j.notes + ["agent did not submit; baseline facts used"]
            return j, transcript
        self._accept(j, submission, request, g, index, tools)
        return j, transcript

    def _create(self, messages):
        for attempt in range(4):
            try:
                return self.client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS,
                    output_config={"effort": self.effort},
                    cache_control={"type": "ephemeral"},
                    system=[{"type": "text", "text": AGENT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                    tools=TOOL_DEFS, messages=messages,
                )
            except anthropic.APIConnectionError:
                time.sleep(2 ** attempt)
        raise RuntimeError("API connection failed repeatedly")

    # ---------------------------------------------------------------- prompt
    def _case_prompt(self, request, g: Gathered, index: EvidenceIndex, tools: CaseTools) -> str:
        cands = []
        for rid in sorted(g.candidates):
            c = g.candidates[rid]
            rec, obs = latest_observation(index, g, rid)
            hint = f"{obs.status} per {rec.evidence_id}" + (" (workflow claim)" if obs.claim else "") if obs else "none"
            links = sorted({f"{l.channel}:{l.evidence_id}" for l in c.links})
            shared = [f"{cid} ({load_dataset().request(cid)['email']})" for cid in sorted(c.shared_with)]
            cands.append({"resource_id": rid, "system_id": c.system_id, "links": ", ".join(links[:6]),
                          "shared_with": ", ".join(shared), "hint": hint})
        others = other_requests(request, g.subject, resolver())
        files = sorted(tools.usable_files.values(), key=lambda e: e["captured_at"])
        return case_message(request, g.subject, cands, others, files)

    # ---------------------------------------------------------------- checks
    def _accept(self, j: Judgment, submission: dict, request, g: Gathered, index, tools: CaseTools):
        usable_ids = tools.usable_evidence_ids()
        requested_at = parse_ts(request["requested_at"])
        j.rejected = submission.get("rejected", [])
        j.notes.append("summary: " + submission.get("summary", ""))
        facts: dict[str, Fact] = {}
        for f in submission.get("facts", []):
            rid = f["resource_id"].strip()
            if not RESOURCE_RE.match(rid) or f["category"] not in categories() or f["state"] not in STATES:
                j.notes.append(f"dropped malformed fact {f}")
                continue
            if not any(requested_at <= r.captured_at <= g.audit_at for r in index.by_resource.get(rid, [])):
                j.notes.append(f"dropped out-of-episode fact {rid}: no usable evidence since requested_at")
                continue
            system = index.system_of(rid) or f["system_id"]
            evidence = f["evidence_id"].strip()
            if evidence not in usable_ids:
                rec, _ = latest_observation(index, g, rid)
                j.notes.append(f"{rid}: evidence {evidence!r} not usable; replaced with {rec.evidence_id if rec else 'none'}")
                evidence = rec.evidence_id if rec else ""
            facts[rid] = Fact(rid, system, f["category"], f["state"], evidence, f.get("reason", ""),
                              bool(f.get("condition_met", True)))
        j.facts = list(facts.values())
