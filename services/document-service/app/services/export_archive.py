"""Reads previously generated export files back out of the export archive.

Exports are rendered to disk by the export worker under ``EXPORT_ARCHIVE_DIR``
(optionally in per-folder subdirectories) and served back to the caller by name.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

logger = structlog.get_logger()

DEFAULT_ARCHIVE_DIR = "/var/lib/otterworks/exports"


class ExportArchive:
    """Serves rendered export files from the archive directory."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or os.environ.get(
            "EXPORT_ARCHIVE_DIR", DEFAULT_ARCHIVE_DIR
        )

    def read_export(self, name: str) -> str:
        """Return the contents of the named export.

        ``name`` may include a subdirectory (``"reports/q3.md"``). Raises
        ``FileNotFoundError`` when the export does not exist, and refuses any
        name that resolves outside the archive root.
        """
        path = self._resolve(name)
        logger.debug("export_read", name=name)
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def _resolve(self, name: str) -> Path:
        """Return the path of ``name`` inside the archive, or refuse it.

        Containment is decided after resolution, so ``..`` segments and symlinks
        are already collapsed by the time the path is compared to the root.
        """
        root = Path(self.base_dir).resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise FileNotFoundError(
                f"[Errno 2] No such file or directory: {str(root / name)!r}"
            )
        return path
