"""Explicit, append-only local exports into an existing company directory."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from financials.statements import normalize_statements, reconcile, STATEMENTS


def _clean(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(_clean(value), stream, ensure_ascii=False, allow_nan=False, indent=2)


def prepare_workpapers(payloads, *, ticker, as_of, currency, company_dir, write=False):
    """Dry-run by default; explicit writes create immutable candidate versions.

    Existing files, human models and current pointers are never updated. A failed
    export keeps its INCOMPLETE marker and must be investigated before retrying;
    this is not yet a cloud task/resume implementation.
    """
    root = Path(company_dir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("company_dir must be an existing non-symlink directory")
    package = normalize_statements(payloads, ticker=ticker, as_of=as_of, currency=currency)
    package["checks"] = reconcile(package)
    encoded = json.dumps({"normalized": package, "retained_sources": _clean(payloads)},
                         sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    version = as_of.replace("-", "") + "_" + hashlib.sha256(encoded).hexdigest()[:12]
    data_relative = Path("02_财务数据") / version
    working_relative = Path("04_研究底稿") / version
    for relative in (data_relative, working_relative):
        for candidate in (root / relative.parts[0], root / relative):
            if candidate.is_symlink():
                raise ValueError("managed export paths must not be symlinks")
    result = {"version_id": version, "written": False, "data_relative_path": data_relative.as_posix(),
              "working_relative_path": working_relative.as_posix(), "status": package["status"],
              "annual_periods": package["annual_periods"], "recent_periods": package["recent_periods"]}
    if not write:
        return result
    destinations = (root / data_relative, root / working_relative)
    if any(p.exists() for p in destinations):
        raise FileExistsError("candidate version already exists; no files overwritten")
    for p in destinations:
        p.parent.mkdir(exist_ok=True)
        p.mkdir(exist_ok=False)
        (p / "INCOMPLETE").write_text("Candidate export in progress; not a published model.\n", encoding="utf-8")
    data_dir, working_dir = destinations
    for kind in STATEMENTS:
        _write_json(data_dir / f"source_{kind}.json", payloads[kind])
    _write_json(data_dir / "normalized.json", package)
    with (data_dir / "facts.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["statement", "period_start", "period_end", "basis", "field", "value", "unit", "published_at", "source_row", "revision_id"])
        for kind, rows in package["statements"].items():
            for row in rows:
                for field, value in row["values"].items():
                    writer.writerow([kind, row["period_start"], row["period_end"], row["period_basis"], field, value,
                                     row["units"][field], row["published_at"], row["source_row"], row["revision_id"]])
    with (data_dir / "checks.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["period_end", "check", "status", "difference_CNY", "missing_inputs", "note"])
        for item in package["checks"]:
            writer.writerow([item["period_end"], item["check"], item["status"], item["difference"],
                             ",".join(item.get("missing_inputs", [])), item.get("note", "")])
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in data_dir.iterdir() if p.is_file() and p.name != "INCOMPLETE"}
    manifest = {"schema_version": 1, "version_id": version, "ticker": ticker, "as_of": as_of,
                "created_at": datetime.now(timezone.utc).isoformat(), "human_review": "pending",
                "cloud_sync_verified": False, "publication_status": "candidate", "files_sha256": files,
                "working_relative_path": working_relative.as_posix()}
    _write_json(data_dir / "manifest.json", manifest)
    (working_dir / "README.md").write_text(
        f"# 财务底稿候选版本 {version}\n\n"
        f"数据位置：../../{data_relative.as_posix()}\n\n"
        "原始数据、标准化三表、逐字段单位与来源行、勾稽检查分开保存。\n"
        "本版本仅为历史财务候选底稿，不是批准后的预测模型。人工状态待核验；尚未证明 OneDrive 云端同步。\n"
        "现金及现金等价物与货币资金口径不同，需附注调节。缺项不按零处理。\n"
        "原有人工模型和正式版本指针保持原样。失败导出会保留 INCOMPLETE 标记。\n", encoding="utf-8")
    for p in destinations:
        (p / "INCOMPLETE").unlink()
    return dict(result, written=True)
