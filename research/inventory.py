"""Metadata-only inventory of existing company materials, without file hydration."""
from datetime import datetime, timezone
import os
from pathlib import Path


def scan_company(company_dir, *, max_files=500):
    root = Path(company_dir)
    if root.is_symlink() or not root.is_dir() or isinstance(max_files, bool) or max_files < 1:
        raise ValueError("expected an existing company directory and positive max_files")
    records, truncated = [], False
    for directory, children, files in os.walk(root, followlinks=False):
        children[:] = sorted(name for name in children if not name.startswith(".")
                              and name not in {"02_财务数据", "03_财务模型", "04_研究底稿", "05_历史版本"}
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
            "note": "File metadata does not establish readability, source authority, reporting date or cloud synchronization."}
