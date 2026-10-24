# Token Usage and Cost Report

Final full-dataset run that produced `output.csv` (518 requests).

## Run

| item | value |
|---|---|
| command | `python code/main.py --mode agent --budget-usd 15` (session 1 with `--workers 8`, session 2 with `--workers 2`) |
| judge version | `74a13b21acb6` (hash of all `deletion_audit/` code, prompts, tool schemas, image transcriptions) |
| requests | 518 (COMPLETE 153, INCOMPLETE 355, NOT_APPLICABLE 10) |
| sessions | 2: the first judged 517 cases and was stopped by the host for low memory; the resumed run reused those 517 checkpoints (no model calls) and judged the last case |
| output | `output.csv`: 518 rows, 0 contract errors, every row identical to its version checkpoint |
| fallbacks to baseline (agent never submitted) | 0 |

## Models

| provider | model | used for |
|---|---|---|
| Anthropic (Claude API, Python SDK) | `claude-haiku-5-5` | intent classification (structured output) and the tool-calling investigation loop |

Only one model is used, so the per-model totals equal the overall totals below. Baseline mode, gathering, the step builder and validation make no model calls.

## Totals for the final run (per model = overall)

| metric | value |
|---|---|
| model calls | 4,621 (8.9 per request) |
| input tokens, uncached | 558,350 |
| input tokens, cache write | 29,133,267 |
| input tokens, cache read | 126,273,509 |
| input tokens, all | 155,965,126 |
| output tokens (incl. thinking) | 3,814,429 |
| total tokens | 159,779,555 |
| average tokens per request | 308,455 |
| median / max tokens per request | 286,720 / 837,770 |
| **estimated total cost** | **$7.02** |
| **estimated cost per request** | **$0.0136** |
| max cost of a single request | $0.0749 |

## By stage

| stage | calls | uncached input | cache write | cache read | output | est. cost |
|---|---|---|---|---|---|---|
| intent classification | 518 | 549,124 | 0 | 0 | 51,521 | $0.08 |
| investigation (tool loop) | 4,103 | 9,226 | 29,133,267 | 126,273,509 | 3,762,908 | $6.94 |

Investigation turns per deletion request: mean 8.1, median 8, max 16 (turn cap 16; 2 requests reached it and submitted on the final turn). The 10 NOT_APPLICABLE requests stop after the intent call.

## Pricing assumptions

| token type | USD per million, prompt <= 100K | USD per million, prompt > 100K |
|---|---|---|
| input (uncached) | 0.10 | 0.50 |
| cache write (1.25x input) | 0.125 | 0.625 |
| cache read (0.1x input) | 0.01 | 0.05 |
| output | 0.50 | 2.50 |

List prices for `claude-haiku-5-5`, applied per call by prompt size. 9 of 4,621 calls had a prompt above 100K tokens (largest 130,935: late turns of long investigations) and are priced at the long-prompt tier; they account for $0.19 of the total. Costs are estimates; the provider invoice is authoritative.

Prompt caching does most of the work: the static system prompt and tool schemas are cached across requests and the growing conversation is cached across turns, so 81% of input tokens are cache reads.

## One-time preprocessing (not part of the final run)

The 11 evidence screenshots were transcribed once with the same model and cached in `code/cache/image_text/`; the final run reads the cache and makes no vision calls.

| stage | model | calls | input | output | est. cost |
|---|---|---|---|---|---|
| image transcription | `claude-haiku-5-5` | 11 | 30,914 | 7,142 | $0.0067 |

## Overall including preprocessing

| model | calls | total tokens | est. cost |
|---|---|---|---|
| `claude-haiku-5-5` | 4,632 | 159,817,611 | $7.03 |

Development and evaluation runs (dev, holdout and custom-case runs) are not included above; they are described in `evaluation_report.md`. Unit tests use a fake client and make no calls.
