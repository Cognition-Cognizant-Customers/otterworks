#!/usr/bin/env python3
"""Import the user-activity notebook as a module.

The notebook `databricks/notebooks/user_activity_daily.py` is the single source of
truth for the conversion's SQL. The landing helper and the recon script import it
from disk so they execute the exact statement text the job task executes — never a
second copy that could drift from it.
"""

from __future__ import annotations

import importlib.util
import os
from types import ModuleType

NOTEBOOK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "databricks", "notebooks", "user_activity_daily.py",
)


def load_pipeline(path: str = NOTEBOOK_PATH) -> ModuleType:
    """Load the notebook as a module (its Databricks entrypoint is guarded)."""
    spec = importlib.util.spec_from_file_location("ow_tp_user_activity_pipeline", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load pipeline notebook at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
