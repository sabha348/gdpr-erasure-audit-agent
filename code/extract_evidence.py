"""Entry point: python code/extract_evidence.py [--no-llm] [--only ids] [--force]  (see extraction/run.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extraction.run import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
