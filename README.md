# Ghost Data Still Exists: a deletion-audit agent

My solution to the HackerRank Orchestrate (October 2026) challenge "Ghost Data Still Exists".

This project is built on HackerRank's starter repository, [interviewstreet/hackerrank-orchestrate-october26](https://github.com/interviewstreet/hackerrank-orchestrate-october26), which provides the problem statement, the dataset in `dataset/` and the agent instructions in `AGENTS.md`. The commit history before my submission commit is theirs.

## The challenge

A deletion workflow can report a customer's data as deleted while copies survive in secondary databases, logs, support attachments, exports, search indexes and backups, and sync or restore jobs can bring deleted data back. For each of 518 requests, the system must decide whether deletion was actually requested and whether it is truly `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE`, then produce the ordered finding, remediation and verification steps, each backed by evidence. The full contract is in [`problem_statement.md`](./problem_statement.md).

## Architecture

The system is a single tool-calling agent (Claude Haiku 5.5, `claude-haiku-5-5`, through the Anthropic SDK) inside a deterministic Python pipeline. The model only makes the judgments that need reading and reasoning. Everything that can be computed is computed in code.

1. **Evidence index.** Built once. Every SQL row, JSON object, log trace, text line, PDF line and image transcription in `dataset/evidence/` becomes a record with its capture time, system, resource ids and identifiers.
2. **Intent.** One structured-output call decides whether the request text is a deletion request, in any language. If it is not, the case ends as `NOT_APPLICABLE` with no steps.
3. **Deterministic gathering.** Python resolves the subject's current and historical identifiers and collects candidate resources from records captured before the audit time. Each candidate is tagged with how it was linked.
4. **Investigation loop.** The agent starts from the candidate list and can reject candidates or look for missed records. It has tools to search evidence, query SQL snapshots, read log traces, read system activity and look up deletion rules. It decides when it is done by calling `submit_facts`. A hard cap of 16 turns forces a submission.
5. **Guard rails.** Python checks every submitted fact before it is used: the category and state vocabulary, whether the evidence was usable at audit time, whether the record is inside the request's episode, and attribution rules for deletion-worker runs shared with other requests.
6. **Step builder and validator.** Deterministic code turns facts into ordered steps from `deletion_rules.csv`, and a validator enforces the output schema, allowed operations and states, phase order and evidence timing.

Design notes and build rules are in [`code/PLAN.md`](./code/PLAN.md).

### Why a single agent

The hard part of each case is one connected investigation. The agent has to tell which records belong to the subject, which copy of a record is authoritative, and whether a "deleted" claim is contradicted by a replica or a later sync. Those judgments depend on each other and on the same evidence. Splitting them across several agents would mean passing partial context between them, adding cost and new failure points at each handoff, and giving no clear gain on a task with one subject and one evidence set per case. The parts that are not judgment calls, such as identifier resolution, time filtering, rule lookup and step ordering, are better as plain code than as extra agents. With one agent and a deterministic frame, the full 518-case run cost about $7 (about $0.014 per request), and failures were easy to trace to a single judgment.

### Why not purely rule-based

The challenge notes that a purely deterministic solution solves roughly 20 to 30 percent of the problem, and my own baseline confirmed it. Keyword intent matching misses follow-ups such as "confirm that my profile is gone" and fails on requests in other languages. A fixed mapping from resource prefix to data category gets secondary copies wrong. Taking the latest observation at face value cannot weigh a workflow's claim against a surviving replica. Rules are still central to the design. They do the gathering, enforce scope and build the steps, and the model is used only where reading the evidence is required.

## Evaluation method

### Dev and holdout split

The only labelled data is the 32 public samples. I split them once into 20 dev cases and 12 holdout cases, stratified by status, with a fixed seed (`code/evaluation/split.json`). All debugging and prompt changes used dev only. Holdout was scored only at milestones, only as aggregate numbers, and individual holdout cases were not inspected. Holdout is therefore the unbiased estimate, and dev is optimistic.

### No-LLM baseline ablation

Running `python code/main.py --mode baseline` uses the same evidence index, gathering, step builder and validator with the model removed. Intent is a keyword match, every gathered candidate is accepted, category comes from a fixed mapping, and state is the latest observation. Because only the judgment stage differs, the gap between the two modes measures what the agent adds.

| metric (dev, 20 cases) | baseline, no LLM | agent |
|---|---|---|
| status accuracy | 70.0% | 100.0% |
| step precision | 45.6% | 96.2% |
| step recall | 65.3% | 86.4% |
| step F1 | 53.7% | 91.1% |
| exact sequence match | 15.0% | 45.0% |

On holdout, the final version scored 100% status accuracy in both runs, with step F1 of 87.8% and 92.1% across two runs of identical code (baseline: 91.7% status, 67.7% step F1). On 13 hand-written edge cases, the agent passed 13 and the baseline passed 3. These cases cover non-English requests, negation, withdrawal, third-party requests, prompt injection, tombstones and expired retention.

## Results

On the official challenge scoring, the submission scored **80.6 / 100** overall, with a **code score of 25.5 / 30**.

The final run judged all 518 requests with 0 format errors and 0 fallbacks to the baseline. It produced 355 `INCOMPLETE`, 153 `COMPLETE` and 10 `NOT_APPLICABLE` results. Full metrics, the iteration history and every fix are in [`code/evaluation/evaluation_report.md`](./code/evaluation/evaluation_report.md).

## How to run

Use Python 3.13 and run every command from the repository root.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
python -m pip install -r code/requirements.txt
```

Agent mode needs an Anthropic API key. Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Baseline mode, the tests and the results UI need no key.

```bash
python code/main.py                                   # agent mode, writes output.csv
python code/main.py --workers 8 --budget-usd 15       # 8 parallel cases, stop launching at about $15
python code/main.py --mode baseline                   # deterministic baseline, no LLM
```

Each case is checkpointed under `code/runs/agent/eval/v-<version>/`, where the version is a hash of the judge code, prompts and image transcriptions. Rerunning resumes without paying again for finished cases. With the shipped checkpoints, the agent command rebuilds `output.csv` without any model call. Any code or prompt change produces a new version, so stale checkpoints are never reused.

```bash
python code/evaluation/score.py --set dev              # baseline vs agent on the dev split
python code/evaluation/custom_cases.py --modes agent   # hand-written edge cases
python -m unittest discover -s code/tests -v           # unit tests, no API calls

python -m pip install -r code/ui/requirements.txt
streamlit run code/ui/app.py                           # read-only results viewer
```

## Limitations

**Shared deletion-batch attribution (case_63).** The deletion workflow often processes several customers' requests in one batch run. Some records in those runs carry no identifier, name or resource link to any one customer, such as empty account lookups or all-null analytics profiles. The system rejects these records instead of guessing an owner. This keeps precision high but costs recall, because the subject's own identity-free records are missed when their run is shared. The clearest case is `case_63`, a valid deletion request where all seven candidates came only from a run shared with two other requests. One further candidate was rejected because a screenshot showed it held a different person's details, which contradicted the data catalog. The output for that case is `COMPLETE` with no steps, the only `COMPLETE` result without verification steps. Fixing it would need a policy change and a full re-run for one case, so I left it as is.

**Position-based attribution was tested and rejected.** I tested whether a record's position inside a shared batch run identifies its owner, for example the i-th account in the run belonging to the i-th request. On dev gold labels, owner order reversed between record types in 6 of 10 comparable pairs, and rank attribution was 29 to 42 percent accurate depending on the ordering, at or below chance. Simulated on the saved dev judgments, the best ordering recovered 4 correct records but added 13 wrong ones. It lowered step F1 from 90.6% to 83.9% and turned one correct `COMPLETE` case into `INCOMPLETE`. The reject policy was kept. Details are in `code/evaluation/position_test.py` and `code/evaluation/results/position_test.md`.

**Run-to-run variance.** The agent is not fully deterministic. Two dev runs of identical code agreed on aggregate step F1 to within 0.5 points, but 3 of 20 individual cases changed. Two holdout runs of identical code differed by 4.3 points of step F1, which is large because one case moves the 12-case holdout score by about 4 to 8 points. Single-case results and small holdout differences should be read as noisy. The differences were in operation, state or evidence choices on correct resources. Which resources were found stayed the same.

Other known gaps include one dev resource that appears in no supplied file, and edge cases such as identity collisions that the dataset does not contain. These are documented in the evaluation report and `code/PLAN.md`.

## Repository layout

| path | contents |
|---|---|
| `code/deletion_audit/` | evidence index, gathering, agent, tools, prompts, policy, step builder, validator |
| `code/main.py` | entry point for both modes |
| `code/evaluation/` | scorer, split, custom cases, coverage and position tests, reports |
| `code/runs/` | checkpoints of the final runs |
| `code/tests/` | unit tests with a scripted fake model client |
| `code/ui/` | Streamlit viewer for saved results |
| `dataset/` | challenge data provided by HackerRank |
| `output.csv` | final submission |
