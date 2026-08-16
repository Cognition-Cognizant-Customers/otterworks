import sys
from pathlib import Path

# The migration modules are flat scripts (run via `uv run <script>.py`), so the
# workload directory has to be importable the same way it is at runtime.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
