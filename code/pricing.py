"""Cost estimates for claude-haiku-5-5 usage records (single source for the run's
budget cap, the usage report and the UI). Outside deletion_audit/, so it does not
affect the checkpoint version hash.

A call whose prompt (uncached + cache-write + cache-read input) exceeds 100K tokens
is billed at the long-prompt tier for the whole call. Cache writes cost 1.25x and
cache reads 0.1x the tier's input price. List prices, USD per million tokens.
"""

LONG_PROMPT = 100_000
PRICE = {"input_tokens": 0.10, "output_tokens": 0.50,
         "cache_creation_input_tokens": 0.125, "cache_read_input_tokens": 0.01}
PRICE_LONG = {"input_tokens": 0.50, "output_tokens": 2.50,
              "cache_creation_input_tokens": 0.625, "cache_read_input_tokens": 0.05}
KEYS = list(PRICE)


def prompt_tokens(u: dict) -> int:
    return sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def is_long(u: dict) -> bool:
    return prompt_tokens(u) > LONG_PROMPT


def call_cost(u: dict) -> float:
    table = PRICE_LONG if is_long(u) else PRICE
    return sum((u.get(k, 0) or 0) * table[k] for k in KEYS) / 1e6


def usage_cost(usage: list[dict]) -> float:
    return sum(call_cost(u) for u in usage)
