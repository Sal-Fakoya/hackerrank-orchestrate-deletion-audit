"""One-time transcription of PNG evidence into plain text with Claude vision.

Results are cached under code/cache/image_text/<file_id>.txt together with a
usage record, so the index never calls the API once the cache is warm.

    python -m deletion_audit.image_text        (from code/)
"""

import base64
import json
import sys

from dotenv import load_dotenv

from .data import load_dataset
from .paths import CACHE_DIR, DATASET_DIR, ENV_FILE

MODEL = "claude-haiku-5-5"
IMAGE_CACHE = CACHE_DIR / "image_text"

PROMPT = """This is a screenshot of an internal business application.
Transcribe every piece of visible text exactly as written: identifiers, emails,
phone numbers, hashes, resource ids, statuses, labels, timestamps and table cells.
Rules:
- One table row per line, cells separated by " | ", header row first.
- Label/value pairs as "label: value", one per line.
- Copy identifiers character for character; never correct, complete or guess them.
- If text is cut off or illegible, write [illegible] in its place.
- Do not summarise, interpret or add commentary. Output only the transcription."""


def cache_path(file_id: str):
    return IMAGE_CACHE / f"{file_id}.txt"


def cached_text(file_id: str) -> str | None:
    path = cache_path(file_id)
    return path.read_text(encoding="utf-8") if path.exists() else None


def transcribe_all(force: bool = False) -> list[dict]:
    """Transcribe every PNG in the manifest that is not cached yet."""
    import anthropic

    load_dotenv(ENV_FILE)
    client = anthropic.Anthropic()
    IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    usage_log = []
    for entry in load_dataset().manifest:
        if not entry["file_path"].endswith(".png"):
            continue
        file_id = entry["file_id"]
        if cached_text(file_id) is not None and not force:
            continue
        data = base64.standard_b64encode((DATASET_DIR / entry["file_path"]).read_bytes()).decode("ascii")
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            output_config={"effort": "low"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                    {"type": "text", "text": PROMPT},
                ],
            }],
        )
        if response.stop_reason != "end_turn":
            raise RuntimeError(f"{file_id}: unexpected stop_reason {response.stop_reason}")
        text = "".join(b.text for b in response.content if b.type == "text")
        cache_path(file_id).write_text(text, encoding="utf-8")
        record = {
            "file_id": file_id,
            "model": MODEL,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        usage_log.append(record)
        print(json.dumps(record), file=sys.stderr)
    if usage_log:
        with (IMAGE_CACHE / "usage.jsonl").open("a", encoding="utf-8") as f:
            for record in usage_log:
                f.write(json.dumps(record) + "\n")
    return usage_log


if __name__ == "__main__":
    transcribe_all(force="--force" in sys.argv)
