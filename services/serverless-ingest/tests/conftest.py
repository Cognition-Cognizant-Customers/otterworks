from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
