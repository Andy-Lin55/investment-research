"""Metadata-only inventory of existing company materials, without file hydration."""
from datetime import datetime, timezone
import os
import re
from pathlib import Path


def scan_company(company_dir, *, max_files=500):
    root = Path(company_dir)
    if root.is_symlink() or not root.is_dir() or isinstance(max_files, bool) or max_files < 1:
        raise ValueError("expected an existing company directory and positive max_files")
    records, truncated = [], False
    for directory, children, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        generated_parent = relative.as_posix() in {"02_财务数据", "04_研究底稿"}
        children[:] = sorted(name for name in children if not name.startswith(".")
                              and not (generated_parent and re.fullmatch(r"\d{8}_[0-9a-f]{12}", name))
                              and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if name.startswith(".") or path.is_symlink():
                continue
            if len(records) >= max_files:
                truncated = True
                break
            try:
                info = path.stat()
                records.append({"relative_path": path.relative_to(root).as_posix(), "extension": path.suffix.lower(),
                                "bytes": info.st_size, "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                                "content_inspected": False, "human_review": "pending"})
            except OSError as error:
                records.append({"relative_path": path.relative_to(root).as_posix(), "content_inspected": False,
                                "error": type(error).__name__, "human_review": "pending"})
        if truncated:
            break
    return {"schema_version": 1, "files": records, "truncated": truncated,
            "note": "Only exporter-version directories under 02/04 are excluded; existing models/data/archives remain. File metadata does not establish readability, source authority, reporting date or cloud synchronization."}
