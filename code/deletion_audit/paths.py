"""Repository-relative paths. Nothing here depends on the working directory."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"
CODE_DIR = REPO_ROOT / "code"
CACHE_DIR = CODE_DIR / "cache"
EVALUATION_DIR = CODE_DIR / "evaluation"
OUTPUT_CSV = REPO_ROOT / "output.csv"
# Loaded with python-dotenv only; never read or printed directly.
ENV_FILE = REPO_ROOT / ".env"
