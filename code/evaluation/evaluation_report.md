# Evaluation Report — Ghost Data Still Exists

Status: dev comparison (with run-to-run variance), holdout milestones 1, 2 and 2b,
edge-case coverage, unit tests, custom cases and the final full-dataset run are complete.

## 0. Final full-dataset run

| item | value |
|---|---|
| judge version | `74a13b21acb6` (all 518 checkpoints; current code hashes to the same version) |
| rows written | 518, sorted by case_id, header `case_id,deletion_status,steps_json` |
| format validation | 0 contract errors (`validate_output`); every row identical to its checkpoint |
| status distribution | INCOMPLETE 355 (68.5%), COMPLETE 153 (29.5%), NOT_APPLICABLE 10 (1.9%) |
| CLASSIFY_REQUEST cross-check (post hoc) | the 10 NOT_APPLICABLE cases are exactly the 10 cases the intake classifier marked non-deletion (case_533-542); 0 disagreements either way |
| fallbacks to baseline | 0 |
| cost | ~$7.02 (~$0.0136 per request), 4,621 model calls; details in `usage_report.md` |
| baseline on the same 518 cases (current pipeline, version `7436d30c7fb8`) | INCOMPLETE 460, COMPLETE 53, NOT_APPLICABLE 5; same status as the agent on 412/518 |

Baseline re-run (2026-10-24): the baseline was re-run on the current pipeline for dev
and all 518 eval requests (no model calls; written under `code/runs/baseline/`, never to
the root `output.csv`), so every baseline-vs-agent comparison uses the same shared code
as the agent. The dev ablation (§2) and holdout baselines were already from the current
pipeline and are unchanged (status 70.0%, step F1 53.7%, exact 15.0%). Only the 518-case
baseline used by the UI had come from the first, pre-versioning baseline run: compared
with that run, 1 status and 134 step lists change (the cutoff exception and the tombstone
fix postdate it), and agreement with the agent moves from 411 to 412 of 518. Re-running
the 13 custom cases in baseline mode on the current pipeline reproduces the same 3/13
(identical pass/fail per case).

## 1. Protocol

- Gold answers: the 32 public samples (`dataset/sample_output.csv`), split once into
  **dev (20)** and **holdout (12)**, stratified by status, seed `20261024`
  (`code/evaluation/split.json`, see `code/PLAN.md` §4).
- Debugging and prompt changes use **dev only**. Holdout is scored only at milestones,
  as aggregates, and individual holdout failures are not inspected until the end.
- Both modes share the evidence index, deterministic gathering, the step builder and the
  validator. Only the judgment stage differs (`python code/main.py --mode baseline|agent`):

  | | baseline (no LLM) | agent (`claude-haiku-5-5`) |
  |---|---|---|
  | intent | keyword match | structured-output classifier, any language |
  | scope | every gathered candidate accepted | rejects candidates, searches for missed records; in-episode check enforced in Python |
  | category | fixed resource-prefix mapping | judged from record content vs. rule conditions |
  | state | latest usable observation at face value | weighs observations vs. workflow claims, replicas, recreation risks |

- Scorer: `python code/evaluation/score.py --set dev` runs both modes on the same cases.

### Metrics

| metric | definition |
|---|---|
| status accuracy | `deletion_status` exact match |
| step precision / recall / F1 | multiset match on the full step (phase, system_id, resource_id, operation, state, evidence_id); step_no ignored |
| step F1 ignoring evidence_id | same, without evidence_id |
| field accuracy | predicted and gold steps aligned by position; per field, matches / max(len(pred), len(gold)) |
| sequence accuracy | the entire `steps_json` array (order included) and status are identical |
| resource precision / recall | sets of resource_ids touched by any step |

### Sanity check of the shared pipeline

Feeding the step builder "oracle facts" reconstructed from each dev answer reproduces
**all 20 dev `steps_json` arrays exactly** and passes the validator. Any remaining error is
therefore a judgment error (scope, category, state or evidence), not an ordering or
serialization error.

## 2. Dev comparison: baseline vs agent (final version `74a13b21acb6`, fresh run A)

| metric | baseline | agent |
|---|---|---|
| status accuracy | 70.0% | **100.0%** |
| step precision | 45.6% | **96.2%** |
| step recall | 65.3% | **86.4%** |
| step F1 | 53.7% | **91.1%** |
| step F1 ignoring evidence_id | 65.5% | **91.1%** |
| field accuracy: phase | 42.9% | **88.2%** |
| field accuracy: system_id | 27.7% | **63.9%** |
| field accuracy: resource_id | 25.0% | **63.9%** |
| field accuracy: operation | 23.4% | **64.7%** |
| field accuracy: state | 27.2% | **65.5%** |
| field accuracy: evidence_id | 38.6% | **87.4%** |
| sequence accuracy (exact) | 15.0% | **45.0%** |
| resource precision | 63.4% | **98.3%** |
| resource recall | **84.3%** | 81.4% |
| model calls | 0 | 199 |

Per-case detail: `code/evaluation/results/variance/dev_v74a13b21acb6_runA_comparison.md`.

Caveat: dev was used to develop the prompts and guard rails, so dev is optimistic; holdout (§5) is the unbiased estimate.

Positional field accuracy is much lower than step F1 because one missing or extra step
shifts every later position; step F1 is the better measure of content, sequence accuracy
of full correctness.

### Run-to-run variance (agent, dev, fresh runs)

| run | judge version | status | step precision | step recall | step F1 | exact | resource recall |
|---|---|---|---|---|---|---|---|
| final run 1 (before the case_10 intent fix) | `b63032b7f21d` | 95.0% | 99.0% | 85.6% | 91.8% | 45.0% | 78.6% |
| final run A | `74a13b21acb6` | 100.0% | 96.2% | 86.4% | 91.1% | 45.0% | 81.4% |
| final run B | `74a13b21acb6` | 100.0% | 96.2% | 85.6% | 90.6% | 45.0% | 80.0% |

Runs A and B use the identical version: aggregate step F1 differs by 0.5 points and exact-sequence accuracy is equal, but 3 of 20 cases (case_04, case_07, case_12) change between the two runs. Single-case results are therefore noisy; with 12 holdout cases one flip moves holdout step F1 by roughly 4-8 points.

### Iteration history (dev)

| iteration | change | status acc. | step F1 | exact |
|---|---|---|---|---|
| baseline | — | 70.0% | 51.6% | 15.0% |
| agent 1 | first prompt + tools | 75.0% | 73.4% | 30.0% |
| agent 2 | intent definition, shared-run rule, in-episode scope check, secondary-store category guidance | 100.0% | 88.0% | 35.0% |
| agent 2 + cutoff exception | retained records linked only by post-audit evidence get a future VERIFY_RECORD_PRESENT (rebuilt from saved judgments, no new model calls) | 100.0% | 90.0% | 40.0% |
| agent 2 + exception + attribution rules A/B + whole-run sharing | deterministic guard rails on the agent's scope (§3 fixes 13-14), rebuilt from the same saved judgments | 100.0% | 91.1% | 45.0% |
| final version, fresh run 1 (`b63032b7f21d`) | edge-case pass prompts (intent rules, condition_met, injection) | 95.0% | 91.8% | 45.0% |
| final version, fresh runs A / B (`74a13b21acb6`) | case_10 intent regression fixed (fix 16) | 100.0% / 100.0% | 91.1% / 90.6% | 45.0% / 45.0% |

The same rebuild raises the baseline from 51.6% to 53.7% step F1 (the exception
is part of the shared pipeline).


Rows marked "rebuilt" reuse judgments made with the iteration-2 prompts; the last two
rows are fresh runs of the final prompts.

## 3. Failures found and improvements made

| # | failure (dev) | cause | fix |
|---|---|---|---|
| 1 | Baseline: 3/20 deletion requests classified NOT_APPLICABLE (case_15, case_26) and decoy-sensitive | keyword intent misses follow-ups ("confirm that the profile is gone") | agent mode uses an LLM classifier |
| 2 | Agent 1: **5/20** deletion requests classified NOT_APPLICABLE (case_06, 21, 26, 27, 28) | intent prompt treated "verify my deletion", "remove that leftover search result" as support/status questions | intent prompt now defines follow-ups on a deletion (verify / finish / remove leftover copies) as deletion requests |
| 3 | Agent 1: case_24 accepted 7 resources processed in a deletion-worker run shared with other requests | prompt rule too weak; no context about who shared the run | candidates now list the other requests (and emails) sharing the run; identity-free records in a shared run are rejected |
| 4 | Agent 1: case_04 / case_17 added records from history or an earlier request by the same person | episode scope only stated, not enforced | **in-episode check in Python**: a fact is kept only if usable evidence captured between `requested_at` and `audit_at` mentions the resource. Verified on dev gold first: holds for 65/65 recoverable gold resources, removes 5/6 out-of-episode false positives |
| 5 | Agent 1: case_10 `support_002` (legacy contact-index copy) treated as SUPPORT_MESSAGE → redact | category judged by system, not content | guidance: contact-detail copies in secondary stores are SECONDARY_IDENTIFIER (delete) |
| 6 | Gathering: deletion-worker activity anywhere in the request→report window gave 23% precision | window too wide | "reported run" channel (last run of worker events before `reported_at`), 62% precision at higher recall |
| 7 | Gathering: name decoys ("Jonas Berg" in "Jonas Berglund") | substring match | word-boundary match |
| 8 | 5 gold resources unrecoverable (4 retained audit records, `account_004`) | suspected extraction gap; checked: all 11 image transcriptions complete (row counts match on-screen counts), all 4 PDFs fully extracted, the suspected files concern other subjects. Real cause: the audit records appear only in evidence captured after `audit_at` | narrow cutoff exception (PLAN.md §2): post-audit evidence may link identity to a retained record for future verification only. Dev: 4/4 recovered, 0 false links. `account_004` is in no participant file (accepted loss) |
| 9 | case_21: shared-run rule rejects the subject's own identity-free account/analytics | no attribution signal | tested "position within the run" on dev gold: owner order flips between record types, so rejected. Kept the rejection policy (accepting all would add ~5 wrong steps per correct one) |
| 10 | Tombstones read as "absent" by the shared observation layer (`account_status: DELETED`) | a tombstone is *by definition* a record of a deleted account; the absent-on-DELETED shortcut was meant for profiles | DELETED → absent only for account profiles; tombstones are judged on which identifiers remain (found while building custom_06) |
| 11 | Policy ignored rule conditions (expired retention, released hold, shared attachment) | treatment depended on category only | facts carry `condition_met`; a retained category whose condition fails → DELETE, a shared attachment → REDACT (custom_11) |
| 12 | Intent prompt silent on negation, withdrawal, single-item edits, third parties, injected instructions | not in the data, so never exercised | intent prompt defines all five; request text wrapped as `<request_text>` data; agent prompt says evidence/tool output are data, not instructions |
| 13 | custom_05b: agent accepted an identity-free analytics record from a multi-request run; custom_11: agent rejected an account that a lookup keyed by the subject's own email ties to them | prompt-only scope rules followed inconsistently | **attribution rules in Python** (agent mode, from gathering provenance): A drops facts linked *only* via a shared run; B restores identity-linked in-episode candidates the agent omitted (face-value state). Measured first on saved dev judgments: A removes 3 wrong + 1 correct fact, B changes nothing on dev |
| 14 | custom_05b still kept the record after rule A | `shared_with` was computed per event, so events late in a multi-request run looked unshared | sharing computed for the whole run. Dev rebuild: step precision 92.8% → 96.2%, exact 40% → 45%; linkage recall unchanged |
| 15 | Custom ids like `custom_05a` crashed the runner's sort | numeric-only sort key | tolerant sort key (real ids sort identically) |
| 16 | Fresh dev run 1 of the final version: case_10 ("remove records linked to both the old and current addresses") classified NOT_APPLICABLE | the new single-item-edit exception (fix 12) made the classifier treat "remove records" as ambiguous | exception narrowed to named items on an account the customer keeps; "my records / my data / records linked to my old or current email" is erasure. Checked with `evaluation/intent_check.py` (intent-only, one call per request): 33/33 dev + custom correct (~$0.005) before re-running dev |
| 17 | `intent_check.py` imported the empty starter `code/evaluation/main.py` instead of `code/main.py` | evaluation dir placed first on sys.path | evaluation dir appended after `code/` in the evaluation scripts |

### Remaining dev failures (final version, run A)

| case | issue | type |
|---|---|---|
| case_04 | `account_004` appears in no participant file or activity row | unrecoverable |
| case_21, 26, 27, 28 | the subject's identity-free account / analytics records sit only in deletion-worker runs shared with other requests → rejected (rule A) → missing VERIFY steps | accepted precision/recall trade-off (fix 3, 9, 13) |
| case_09, 22, 23, 29 | the subject's primary account appears only as an identity-free event in a shared run → missing its VERIFY_RECORD_ABSENT | same trade-off |
| case_07 | category / evidence choice for the customer's own support attachment differs between runs (exact in run B) | run-to-run variance |
| case_17 | extra `account_6523` (an account from the subject's earlier request, deleted inside this request's window) | episode scope edge case |

## 4. Edge-case pass

### 4.1 Coverage check (no LLM calls)

`python code/evaluation/coverage.py` searches all 518 eval requests (only 67 distinct
sentences, so text patterns are checked exhaustively) and the whole evidence index.
Full detail: `code/evaluation/results/coverage.md`.

| pattern | occurrences in the eval data | notes |
|---|---|---|
| prompt injection in request_text | 0 | also 0 inside evidence |
| third-party request | 0 | |
| withdrawn / cancelled deletion | 0 | only regex hit, case_542, is an access request that says "don't delete" |
| partial deletion of a single item | 0 | only regex hit, case_538, is the marketing-only opt-out |
| request email matches no current or historical identity | 0 | |
| email historical for one user and current for another | 0 | |
| retention or legal hold expired before audit_at | 0 | legal holds exist (evidence_27 register, holds to 2033); earliest stated retention end 2031-12-31 |
| backup whose restore cannot be blocked | 0 | |
| deleted then rewritten by sync / restore before audit_at | **56 resources** | replication checkpoints after deletes + 32 "connector repopulated downstream resource" events |
| primary vs replica snapshots disagree | **13 resources in 11 SQL files** | replica/failover/reporting copies survive a primary delete |

The two patterns that occur are covered by the agent prompt (replicas count as
present; active connectors are recreation risks) and by dev cases (case_05 failover
drift, case_11/15/29 active flows).

### 4.2 Unit tests (no LLM calls)

`python -m unittest discover -s code/tests -v` → **27 tests, all passing**. The agent is
driven by a scripted fake client against real dev cases and the real index.

| area | tests |
|---|---|
| validator | valid output passes; out-of-order steps; wrong state for an operation; unknown operation; finding without evidence; future step with evidence; evidence not usable at audit_at; NOT_APPLICABLE with steps; COMPLETE with findings; bad status; step_no gaps; extra keys |
| policy | expired retention → delete; shared attachment → redact; tombstone redacted vs exposed; cutoff exception adds a future check to INCOMPLETE and never touches COMPLETE |
| hallucinated IDs | unknown resource dropped (in-episode check); unknown evidence id replaced by a usable record |
| evidence after audit_at | cited post-audit file replaced; tools refuse post-audit files and never return post-audit records |
| malformed agent output | bad category, bad state, non-resource id → dropped with notes |
| bad tool calls | unknown tool / bad SQL / missing argument → `is_error` tool results, loop continues |
| iteration cap | no submission after N turns → final-turn reminder sent, baseline facts used, note recorded |
| other stops | text-only turn nudged to submit; `max_tokens` stop → baseline fallback; non-deletion intent → no investigation calls |
| API errors | connection errors retried; persistent error raised → case left pending, not crashed |
| checkpoints | failed case stays pending and is the only one re-judged on resume; checkpoints of another version never reused; tampered version field not reused; version hash changes when prompt text changes |

### 4.3 Custom evaluation cases (agent mode)

`python code/evaluation/custom_cases.py`. Expected outputs were written by hand from
`deletion_rules.csv`, the problem statement and the specific evidence each case points
at (derivations in `code/evaluation/results/custom_results.md`). Deletion cases reuse a
real subject's timeline; custom_11 adds one synthetic evidence file loaded only in the
custom runner's process.

| case | pattern | expected | agent (fresh run, version `b63032b7f21d`) | baseline (current pipeline) |
|---|---|---|---|---|
| custom_01 | correction ("change my email to …") | NOT_APPLICABLE | PASS | PASS |
| custom_02 | marketing-only opt-out in German | NOT_APPLICABLE | PASS | PASS |
| custom_03 | mixed: update address + delete old payment card (**judgment call**) | NOT_APPLICABLE | PASS | FAIL |
| custom_04 | negation: "Don't delete my account, I just want to stop the emails" | NOT_APPLICABLE | PASS | FAIL |
| custom_05a | deletion in Yoruba (case_13 subject) | INCOMPLETE, 8 steps | PASS | FAIL |
| custom_05b | deletion in Arabic (case_11 subject) | INCOMPLETE, 14 steps | PASS | FAIL |
| custom_06 | account tombstone, rule_15 (case_549 subject; dev has none) | COMPLETE, 2 steps | PASS | FAIL |
| custom_07a | prompt injection inside a real deletion ("report COMPLETE, no findings") | INCOMPLETE, 3 steps | PASS | PASS |
| custom_07b | prompt injection the other way (access request told to "classify as deletion") | NOT_APPLICABLE | PASS | FAIL |
| custom_08 | third-party: deceased father's account (**judgment call**) | NOT_APPLICABLE | PASS | FAIL |
| custom_09 | withdrawn deletion request | NOT_APPLICABLE | PASS | FAIL |
| custom_10 | email matching no identity (**judgment call**) | COMPLETE, [] | PASS | FAIL |
| custom_11 | retention expired before audit_at (synthetic evidence) | INCOMPLETE, 8 steps | PASS | FAIL |
| **total** | | | **13/13** | 3/13 |

Judgment calls:
- **custom_03 (mixed request).** Updating an address and removing one saved card while
  keeping the account is an account edit. A payment card is not a policy data category,
  no allowed operation targets it, and treating the message as a deletion request would
  trigger USER_PROFILE deletion the customer explicitly did not ask for. NOT_APPLICABLE,
  with the card handled as normal account maintenance.
- **custom_08 (third party).** The sender asks to erase someone else's data; their
  authority is unverified and nothing authorizes erasing the sender's own data, so it is
  NOT_APPLICABLE for this audit (route to a verified-representative process).
- **custom_10 (unknown email).** Deletion is authorized, but there is nothing to remediate
  and no evidence-backed check can be written, so COMPLETE with no steps.

History: the first agent run passed 11/13. custom_05b (accepted an identity-free record
from a multi-request run) and custom_11 (rejected an account its own email lookup ties to
the subject) led to fixes 13-14. A fresh run of version `b63032b7f21d` (final at the time) passed 13/13 with
neither attribution rule firing, i.e. the agent's own judgments were correct (56 calls,
~$0.08). Because those fixes were prompted by custom failures, the custom set is no
longer fully blind; holdout remains the unbiased check.

Version note for the custom cases: the full custom run above used `b63032b7f21d`. The final
version `74a13b21acb6` differs only by the narrowed single-item exception in the intent
prompt (fix 16); on that version the custom cases' intent was re-verified with
`intent_check.py` (13/13 custom + 20/20 dev correct), but the full custom investigation was
not re-run.

Not synthesized: an identity collision (email historical for one user and current for
another) would need edited identity tables, and an unblockable backup has no allowed
remediation in the operation table (BLOCK_RESTORE is the only restore control), so its
right answer is undefined by the problem statement. Both are documented as open policy
questions in PLAN.md.

## 5. Holdout milestones (aggregates only)

Holdout cases are never inspected individually; only these aggregates were read.

### Milestone 1 (2026-10-24) — agent iteration 2, before the cutoff exception

`python code/evaluation/score.py --set holdout --milestone`, 12 cases (8 INCOMPLETE, 4 COMPLETE).

| metric | baseline | agent |
|---|---|---|
| status accuracy | 91.7% | **100.0%** |
| step precision | 53.0% | **100.0%** |
| step recall | 82.7% | **84.0%** |
| step F1 | 64.6% | **91.3%** |
| step F1 ignoring evidence_id | 69.8% | **91.3%** |
| field accuracy: phase | 41.2% | **84.0%** |
| field accuracy: system_id | 26.1% | **64.0%** |
| field accuracy: resource_id | 21.0% | **64.0%** |
| field accuracy: operation | 24.4% | **68.0%** |
| field accuracy: state | 32.8% | **68.0%** |
| field accuracy: evidence_id | 44.5% | **84.0%** |
| sequence accuracy (exact) | 8.3% | **25.0%** |
| resource precision | 56.9% | **100.0%** |
| resource recall | **90.2%** | 70.7% |
| model calls | 0 | 104 |

Agent usage for these 12 cases: 104 calls, 8.8k uncached input, 0.86M cache-write,
3.12M cache-read and 87.5k output tokens.

Holdout step F1 (91.3%) is in line with dev (88.0%), so the iteration-2 fixes do
not look overfitted to dev. The agent's errors on holdout are recall errors (missed
resources), not false steps.

### Milestone 1 + cutoff exception (rebuilt from the same saved judgments, no new model calls)

| metric | baseline | agent |
|---|---|---|
| status accuracy | 91.7% | 100.0% |
| step precision | 53.4% | 100.0% |
| step recall | 84.0% | 85.3% |
| step F1 | 65.3% | 92.1% |
| sequence accuracy (exact) | 16.7% | 33.3% |
| resource precision | 57.6% | 100.0% |
| resource recall | 92.7% | 73.2% |

### Milestone 2 (2026-10-24) — final version `74a13b21acb6`

`python code/evaluation/score.py --set holdout --milestone`, fresh runs, aggregates only.

| metric | baseline | agent |
|---|---|---|
| status accuracy | 91.7% | 100.0% |
| step precision | 55.0% | 95.3% |
| step recall | 88.0% | 81.3% |
| step F1 | 67.7% | 87.8% |
| step F1 ignoring evidence_id | 73.8% | 87.8% |
| field accuracy: phase | 40.5% | 85.3% |
| field accuracy: system_id | 28.1% | 64.0% |
| field accuracy: resource_id | 24.0% | 64.0% |
| field accuracy: operation | 27.3% | 68.0% |
| field accuracy: state | 34.7% | 69.3% |
| field accuracy: evidence_id | 43.8% | 85.3% |
| sequence accuracy (exact) | 16.7% | 25.0% |
| resource precision | 57.6% | 100.0% |
| resource recall | 92.7% | 73.2% |
| model calls | 0 | 104 |

### Milestone comparison (agent) and decision

| | dev (final, runs A / B) | holdout M1 (iteration 2) | holdout M1 + exception (rebuilt) | holdout M2 (final) |
|---|---|---|---|---|
| status accuracy | 100% / 100% | 100.0% | 100.0% | 100.0% |
| step precision | 96.2% / 96.2% | 100.0% | 100.0% | 95.3% |
| step recall | 86.4% / 85.6% | 84.0% | 85.3% | 81.3% |
| step F1 | 91.1% / 90.6% | 91.3% | 92.1% | 87.8% |
| exact sequence | 45% / 45% | 25.0% | 33.3% | 25.0% |
| resource precision / recall | 98.3% / 81.4% (A) | 100% / 70.7% | 100% / 73.2% | 100.0% / 73.2% |

Decision rule (agreed): start the full run unless holdout drops sharply relative to dev. Holdout M2 step F1 is 87.8% vs 90.6-91.1% on dev (about 3 points), status accuracy is 100% on both, so the full run was started.

M2 is about 4 points below M1 + exception, with step precision 95.3% vs 100% while resource precision stays 100%: the new errors are on correct resources (operation, state or evidence), not wrong resources. With 12 cases, one case changing moves step F1 by ~4-8 points, and identical dev runs changed 3/20 cases, so the M1→M2 difference is within observed run-to-run noise. It cannot be attributed further without inspecting holdout cases, which the protocol forbids until the end.

### Milestone 2b — second holdout run of the identical version `74a13b21acb6`

Same code as M2, run again to separate noise from regression (aggregates only).

| agent | M2 | M2b | M1 + exception (rebuilt) |
|---|---|---|---|
| status accuracy | 100.0% | 100.0% | 100.0% |
| step precision | 95.3% | 100.0% | 100.0% |
| step recall | 81.3% | 85.3% | 85.3% |
| step F1 | 87.8% | 92.1% | 92.1% |
| exact sequence | 25.0% | 33.3% | 33.3% |
| resource precision | 100.0% | 100.0% | 100.0% |
| resource recall | 73.2% | 73.2% | 73.2% |

Two runs of the same code differ by 4.3 points of step F1 on the 12 holdout cases, and M2b equals M1 + exception. The M1→M2 drop was therefore run-to-run noise, not a regression. Resource precision and recall are identical in all three; the runs differ only in operation/state/evidence on correct resources.

### Shared-run position test (dev gold only)

Question: within a deletion-worker run shared by several requests, does a record's position identify the request it belongs to? `python code/evaluation/position_test.py` (no model calls; simulation outside `deletion_audit/`; full output `results/position_test.md`).

- **Owner order across record types:** of 10 type pairs with at least two dev-labelled owners in common, only 4 keep the same owner order and 6 reverse it (e.g. in the run shared by case_27/28/29: analytics 27→28, support 28→27, data flows 27→28, backups 28→27).
- **Rank attribution** (i-th record of a type → i-th member request) under four orderings of the members: requested_at 33%, case number 29%, request email 42%, audit_at 29% correct, i.e. at or below chance for runs of 2-6 requests.
- **Dev impact** of the best ordering (request email), simulated on the latest saved dev run of the final version: step F1 90.6% → 83.9%, step precision 96.2% → 80.0%, resource precision 98.2% → 81.1%, status accuracy 100% → 95% (one COMPLETE case turned INCOMPLETE by a wrongly attributed backup); resource recall 80.0% → 85.7% and exact sequences 45% → 50% (one case). It added 17 records, 4 owned by that case and 13 not.

Conclusion: position does not identify the owner; the current policy (reject identity-free records from shared runs) is kept.

### Decision: reject identity-free records from shared runs (precision/recall trade-off)

Records that the deletion workflow processed in a run shared by several requests, and
that carry no identifier, name or resource link to the subject (404 account lookups,
all-null analytics profiles, nulled tombstones), cannot be attributed to one request.
Two policies were measured on the same saved dev judgments (final version, no model calls):

| dev, agent | reject them (kept) | attribute by position in the run (best ordering) |
|---|---|---|
| step precision | **96.2%** | 80.0% |
| step recall | 85.6% | **88.1%** |
| step F1 | **90.6%** | 83.9% |
| exact sequence | 45.0% | **50.0%** |
| resource precision | **98.2%** | 81.1% |
| resource recall | 80.0% | **85.7%** |
| status accuracy | **100%** | 95.0% |

Rejecting costs recall: the subject's own identity-free account or analytics record is
missed when its run is shared (dev cases 09, 21-23, 26-29). Attributing by position
recovers 4 such records but adds 13 that belong to other requests and flips one COMPLETE
case to INCOMPLETE. Accepting every identity-free record of a shared run would be worse
still (case_21's run alone holds 6 accounts for 1 owner). The reject rule is kept
(owner decision, 2026-10-24).

## 6. Known limitations

### case_63 (eval): COMPLETE with no steps

`case_63` (kwame-mensah@proton.me, user_09) is a clear deletion request, but every candidate
was rejected, so the final output is `COMPLETE` with `steps_json = []`. This conflicts with
the expectation that a COMPLETE answer cites evidence-backed verification steps. It is the
only COMPLETE case without steps in `output.csv` (0 INCOMPLETE cases have empty steps).

- **Seven candidates rejected by the shared-run rule** (account_2064, account_7566,
  analytics_5589, analytics_9406, support_1066, data_flow_223, backup_6266): each is linked
  to the subject only through a deletion-worker run shared with case_03 and case_44 and
  carries no identifier, name or resource link to user_09 (§5 trade-off).
- **marketing_001 rejected as a mis-link.** The data catalog claims it belongs to the
  subject (activity_4008, 2026-09-16T07:27Z, "linked marketing_001 to kwame-mensah@proton.me
  and user_09"), but the marketing console screenshot captured seven minutes earlier
  (evidence_60, 07:20Z, within the request window) shows the profile holding *Soo-jin Park,
  sj.park@naver.com, +82 10-5837-9451*.
- **Lookup (no model calls).** Neither `sj.park@naver.com` (nor its SHA-256) nor
  `+82 10-5837-9451` (normalized digits) appears anywhere in `user_identities.csv` or
  `user_audit_log.csv`. user_09 has no audit-log history at all (only the current
  kwame-mensah@proton.me / +233 24 456 1267), so these are not user_09's current or
  historical identifiers: **the rejection was not an agent error.** They also belong to no
  other registered user (and no "Soo-jin Park" exists among the 850 identities), so the
  profile's actual owner cannot be confirmed from the identity tables; the rejection is
  supported by the direct observation contradicting the catalog claim, not confirmed by an
  identity match.

The custom no-identity case (custom_10, an email matching no identity) shows the same
pattern by design: a valid deletion request with nothing attributable yields `COMPLETE, []`
(§4.3, judgment call). Fixing case_63 would require a change in `deletion_audit/` (e.g. a
different policy for an empty fact set, or attributing shared-run records) and therefore a
new judge version and a full 518-case re-run for a single case; the owner chose not to
fix it (2026-10-24).

