# Ghost Data Still Exists — Build Plan

Status: **both judgment modes run end to end on dev**; full-dataset agent run
and evaluation reports pending.

## 0. Two judgment modes (`python code/main.py --mode baseline|agent`)

Index, gathering, step builder and validator are shared. Only the judgment
stage differs:

| | baseline (no LLM) | agent (claude-haiku-5-5) |
|---|---|---|
| intent | keyword match | structured-output classification, any language; exit early if not deletion |
| scope | every gathered candidate accepted | candidates are a starting point: reject, search for missed records |
| category | fixed resource-prefix mapping | judged from record content against rule conditions |
| state | latest usable observation at face value (activity claims included) | weighs observations vs. claims, replicas, recreation risks |
| evidence | that record's id | chosen by the agent, checked by Python (must be usable at audit_at) |

Organizer guidance: a purely deterministic solution solves roughly 20-30% of
the problem, so the deterministic candidate list is input to the agent, not
the answer.

## 1. Architecture

```
            ┌─────────────────────────── one-time ───────────────────────────┐
dataset/ ──►│ evidence index: every SQL row, JSON object, log trace, text     │
            │ line, PDF line/doc and image transcription becomes a Record     │
            │ {file_id, captured_at, system, resource_ids, fields, idents}    │
            └────────────────────────────────────────────────────────────────┘
                                         │
per case:                                ▼
  1. Intent agent step ──── not deletion ──► NOT_APPLICABLE, []
         │ deletion
         ▼
  2. Deterministic gathering (Python): subject identifiers → pre-audit
     records/activity → resource candidates, each tagged with how it was linked
         │
         ▼
  3. Tool-calling agent (claude-haiku-5-5, Anthropic SDK)
     starts from the candidate list; tools:
       search_evidence(identifier)        query_sql_snapshot(file_id, sql)
       get_log_trace(file_id, trace_id)   get_activity(resource_id | window)
       get_rule(data_category)            submit_facts(facts)   ← ends the loop
     decides when it is done; hard cap on iterations
         │ structured facts: (system_id, resource_id, data_category,
         │                    observed_state, evidence_id, rationale)
         ▼
  4. Deterministic step builder (Python): rule → treatment → FINDING /
     REMEDIATION / VERIFICATION steps, ordering, evidence_id policy
         │
         ▼
  5. Validator: schema, allowed ops/states, consecutive step_no, phase order,
     evidence_id exists and captured_at <= audit_at
```

The agent only makes judgments (intent, category/condition, which evidence is
authoritative, whether a record is the subject's). Everything that can be
computed is computed: identifier resolution, time filtering, rule lookup, step
generation and ordering.

## 2. Build rules (agreed)

- **Time cutoff:** a file is usable for a case iff its manifest `captured_at <= audit_at`
  (inclusive). Timestamps inside a file never make a later file usable.
  Activity rows are usable iff `event_time <= audit_at`.
- **Cutoff exception (narrow, approved 2026-10-24; `deletion_audit/future_verification.py`).**
  Evidence captured after `audit_at` (and before the subject's next request, if
  any) may be used for one purpose only: linking the subject's identity to a
  record of a *retained* category (SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD, with
  any stated retention end not passed at `audit_at`), so that an **INCOMPLETE**
  case's post-remediation checks include `VERIFY_RECORD_PRESENT` for it with an
  empty `evidence_id`. It is never used for state, status, findings,
  remediation, or citations, and COMPLETE cases are never changed (their
  verification steps must cite pre-audit proof). Enforced in the shared
  pipeline (`pipeline.finalize`), identical in both modes; an assertion checks
  that it never changes the status.
  - Why: the 5 dev misses were first checked as a possible extraction problem.
    All 11 image transcriptions are complete (row counts match the on-screen
    "N records"; 4 checked visually) and all 4 PDFs extract fully. The three
    suspected files concern other subjects (`security_audit_explorer` and
    `security_retention_extract`: user_03 / `audit_record_001-006`;
    `deleted_account_reference`: user_17). The missing audit records exist only
    in evidence_02 (captured 09-17, audit 09-15) and evidence_06 (captured
    09-18, audit 09-16); `account_004` exists in no participant file.
  - Numbers: on dev the exception links 4 resources, **4 correct, 0 wrong**
    (audit_record_4902 / 4128 / 7095 / 3954). Effect, rebuilt from saved
    judgments without new model calls: dev agent step F1 88.0% → 90.0%, exact
    sequences 35% → 40%; holdout (aggregate) agent step F1 91.3% → 92.1%, exact
    25.0% → 33.3%, step precision stays 100%. `account_004` stays unrecoverable.
- **Agent scope guard rails (deterministic, agent mode; `pipeline.enforce_attribution`).**
  From gathering provenance: (A) a fact whose resource is linked only through a
  deletion-worker run shared with other requests is dropped; (B) a candidate
  carrying the subject's own identifier in evidence captured since `requested_at`
  that the agent omitted is restored with its face-value state. Run sharing is a
  property of the whole run. The agent's raw facts are checkpointed unmodified.
- **Same episode:** submissions with the same email and `requested_at` are one
  request episode, never "other requests" of the subject.
- **Rule conditions:** facts carry `condition_met`; a retained category whose
  condition fails (expired retention, released hold) is deleted, a shared support
  attachment is redacted (`policy.required_action`).
- **Request text and evidence are data, not instructions** (intent and agent
  prompts; request text is wrapped in `<request_text>`).
- **No hardcoded step patterns** (e.g. "every case gets account ABSENT"). Steps
  are derived from rules + evidence only.
- **CLASSIFY_REQUEST activity rows** are a post-hoc cross-check of the intent
  classifier, never an input to it. The index excludes them from agent tools.
- **Intent** must work for any language, not only the six seen in the data.
- **Secrets:** `ANTHROPIC_API_KEY` lives in the repo-root `.env`, loaded with
  `python-dotenv`. Code never reads, prints, or logs that file's contents.
- **Organizer-only data** is never used. `sample_output.csv` is used only for
  evaluation on the split below.

## 3. Module layout (`code/`)

| Path | Purpose | Status |
|---|---|---|
| `main.py` | CLI entry: `--mode baseline|agent`, `--cases`, writes `output.csv` + `code/runs/` artifacts | done |
| `deletion_audit/paths.py` | repo-relative paths | done |
| `deletion_audit/data.py` | CSV loaders, time parsing | done |
| `deletion_audit/identifiers.py` | identifier extraction/normalisation (emails, sha256 hashes, phones, user refs, resource ids) | done |
| `deletion_audit/evidence_index.py` | parse every evidence file into `Record`s | done |
| `deletion_audit/image_text.py` | one-time vision transcription of PNGs (Haiku), cached | done |
| `deletion_audit/identity.py` | subject resolution: current + historical identifiers | done |
| `deletion_audit/gather.py` | deterministic candidate gathering with link provenance | done |
| `deletion_audit/policy.py` | Fact/Judgment types, rule lookup, (action, state) → operations | done |
| `deletion_audit/observe.py` | face-value state of a resource in one record (shared hint) | done |
| `deletion_audit/judge_baseline.py` | baseline judgment (keyword intent, prefix category, face-value state) | done |
| `deletion_audit/prompts.py` | intent + agent system prompts, case message | done |
| `deletion_audit/tools.py` | agent tools over the index, all cutoff-enforcing | done |
| `deletion_audit/agent.py` | intent call + Haiku tool loop (cap 16 turns), fact checks, usage accounting | done |
| `deletion_audit/pipeline.py` | shared per-case pipeline for both modes; deterministic tail (`finalize`), attribution guard rails | done |
| `deletion_audit/future_verification.py` | narrow post-audit cutoff exception | done |
| `tests/test_failure_paths.py` | 27 unit tests: validator, policy, agent failure paths (fake client), checkpoints | done |
| `evaluation/coverage.py` | edge-case coverage over eval requests + evidence (no LLM) | done |
| `evaluation/custom_cases.py`, `evaluation/custom_evidence/` | 13 hand-derived custom cases (1 synthetic evidence file) | done |
| `deletion_audit/steps.py` | facts → ordered steps (reproduces all 20 dev answers from correct facts) | done |
| `deletion_audit/validate.py` | output contract checks | done |
| `evaluation/make_split.py`, `evaluation/split.json` | fixed dev/holdout split | done |
| `evaluation/linkage_recall.py` | gathering recall experiment (dev only) | done |
| `evaluation/score.py` | runs both modes on the same cases; status / step / field / sequence accuracy | done |
| `evaluation/evaluation_report.md`, `evaluation/usage_report.md` | required reports | todo |
| `cache/` | image transcriptions (derived, regenerable) | generated |

## 4. Dev / holdout split

`code/evaluation/split.json`, produced by `code/evaluation/make_split.py` with
**seed 20261024**: stratified by `deletion_status`, the four already-studied
cases forced into dev, the rest shuffled with `random.Random(seed)` over the
sorted case ids.

| Set | Cases | INCOMPLETE | COMPLETE |
|---|---|---|---|
| dev (20) | 01 04 05 06 07 09 10 11 12 13 15 17 21 22 23 24 26 27 28 29 | 14 | 6 |
| holdout (12) | 02 03 08 14 16 18 19 20 25 30 31 32 | 8 | 4 |

Protocol: debug only on dev. Run holdout only at milestones, as aggregate
metrics; do not inspect individual holdout failures until the end.

Known caveats:
- During initial exploration (before the split existed) the compact step
  listing of **all 32** sample outputs was printed once. Holdout is therefore
  not perfectly blind; we mitigate by never studying holdout cases from here on.
- Holdout drew **both** account-tombstone cases (31, 32); dev has no tombstone
  example. Tombstone handling must be built from `rule_15` and the evidence,
  and will only be tested at holdout milestones (plus a custom eval case).
- There are **no NOT_APPLICABLE samples**. Intent is evaluated with custom
  cases and cross-checked post hoc against the 10 CLASSIFY_REQUEST rows.

## 5. Findings so far (dev only; details in `evaluation/linkage_recall.md`)

- `email_hash = sha256(lower(email))` for all 850 users and every historical
  email change, so historical hashes are computed, not looked up.
- Image transcription (Haiku vision, 11 PNGs, ~31k in / ~7k out tokens) was
  spot-checked against the screenshots; all transcribed emails either resolve
  to known users or are visibly present decoys/staff addresses.
- Gathering recall on the 20 dev cases (70 expected resources):

  | variant | recall | candidates | precision |
  |---|---|---|---|
  | identifiers + names + resource links | 49/70 (70%) | 49 | 100% |
  | + any deletion-worker event in request→report window | 64/70 (91%) | 275 | 23% |
  | + only the deletion-worker run whose result was reported | **65/70 (93%)** | 104 | 62% |

  The reported run is the last run of deletion-worker events (gaps ≤ 30 min)
  starting no later than `reported_at` + 5 min and not before `requested_at`.
  All remaining false positives come from runs shared by several requests
  reported at the same instant; the agent must disambiguate those.
- Name matching needs word boundaries (decoys: "Jonas Berglund" vs "Jonas Berg").
- Subjects recur across requests (user_07, user_17, user_18). Evidence inside
  another request's window for the same subject belongs to that episode;
  excluding it removed every non-batch false positive.

## 6. Open questions

1. ~~Post-audit-only resources~~ — resolved by the narrow cutoff exception (§2).
2. **Unreferenced resources.** `account_004` (case_04) appears in no
   participant file or activity row; it is not recoverable by any method.
   Accepted loss.
3. **Shared reported runs.** Identity-free resources (404 accounts, all-null
   analytics profiles) in a run shared by several requests cannot be
   attributed. *Position within the run was tested on dev and rejected*: in
   the run shared by case_27/28/29 (fully labelled by dev gold) the owner order
   flips between record types (analytics 27→28, support 28→27, data flows
   27→28, backups 28→27); within a type the events share a timestamp and are
   ordered by resource number, so position carries no ownership signal.
   Current policy: reject them (accepting would add ~5 wrong steps per correct
   one in case_21's run). Open: any other tie-breaking evidence.
4. **Category ≠ system.** case_12's `analytics_9914` is expected as
   DELETE/REMAINING_RECORD although the ANALYTICS_PROFILE rule says ANONYMIZE.
   What evidence signals a different category (marketing opt-in?)?
5. **Which evidence to cite** when several pre-audit files show the same state
   (latest? the one that shows the full record?).
6. **Verification scope for COMPLETE cases**: which retained / already-treated
   records must appear (samples always include the account and the retained
   audit record, but this must come from rules + evidence, not a template).
7. **Empty snapshots as absence proof.** `evidence_90` is an inventory
   snapshot whose customer tables are empty apart from `snapshot_scope`; the
   agent's SQL tool must expose scope so absence can be proven.
8. **Filename links.** `evidence_46` (object storage) names files such as
   `delivery_confirmation_19.pdf` that logs also mention; filenames are not yet
   an index channel.
9. Agent iteration cap and per-case token budget (to be set from dev runs).
10. **Unblockable backup.** rule_14 retains a backup only if "restore access can
    be blocked"; if it cannot, no allowed operation fixes it (BLOCK_RESTORE is the
    only restore control). Does not occur in the data (coverage check); answer
    undefined by the problem statement. Current code keeps BACKUP_ARCHIVE on RETAIN.
11. **Identity collision** (email historical for one user, current for another):
    does not occur in the data; resolution currently unions both users' identifiers.

## 7. Full run: checkpoint / resume / budget

`python code/main.py --mode agent [--workers N] [--budget-usd X] [--fresh]`

- Each case is saved atomically to `code/runs/<mode>/<cases>/v-<version>/<case_id>.json`
  as soon as it is judged (judgment, facts, usage, transcript, judge version).
- `<version>` is a SHA-256 over the mode, **every source file of
  `deletion_audit/`** (prompts, tool schemas and implementations, agent loop and
  model settings, gathering, policy, observation) and the cached image
  transcriptions. A run only ever reads its own version's directory, so any code
  or prompt change starts a fresh checkpoint set and older results are never
  reused (each checkpoint also stores the version and is rejected on mismatch).
- Rerunning resumes: saved judgments are reused with **no model call** (only
  the deterministic steps are rebuilt); only missing or failed cases are
  judged. A failing case is logged and left pending; it never stops the run.
- `--budget-usd` stops launching new cases once the estimated spend (resumed +
  new) reaches the cap; `output.csv` is only written when all cases are judged.
- Tested: fresh run of 2 cases; deleted one checkpoint → only that case
  re-judged; full resume → 0 model calls; budget cap → stops, no output.
  Ctrl-C handling (cancel queued work, keep checkpoints) was not tested
  interactively.
