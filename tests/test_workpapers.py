import importlib
import json

import pytest

from tests.test_financial_statements import inputs


def api():
    try:
        return importlib.import_module("financials.workpapers")
    except ModuleNotFoundError:
        pytest.fail("Versioned financial workpaper export is not implemented")


def test_dry_run_never_creates_or_changes_company_files(tmp_path):
    original = tmp_path / "existing-model.xlsx"
    original.write_bytes(b"original-model")
    result = api().prepare_workpapers(inputs(), ticker="000001.SZ", as_of="2026-10-27",
                                      currency="CNY", company_dir=tmp_path)
    assert result["written"] is False
    assert list(tmp_path.iterdir()) == [original]


def test_explicit_export_preserves_originals_and_contains_sources_checks_and_units(tmp_path):
    (tmp_path / "existing-model.xlsx").write_bytes(b"original-model")
    result = api().prepare_workpapers(inputs(), ticker="000001.SZ", as_of="2026-10-27",
                                      currency="CNY", company_dir=tmp_path, write=True)
    directory = tmp_path / result["data_relative_path"]
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["human_review"] == "pending"
    assert manifest["cloud_sync_verified"] is False
    assert (directory / "source_income.json").exists()
    assert (directory / "facts.csv").exists()
    assert (directory / "checks.csv").exists()
    assert (tmp_path / result["working_relative_path"] / "README.md").exists()
    assert (tmp_path / "existing-model.xlsx").read_bytes() == b"original-model"
    assert "CNY/share" in (directory / "facts.csv").read_text(encoding="utf-8-sig")


def test_reexport_never_overwrites_existing_version(tmp_path):
    args = dict(ticker="000001.SZ", as_of="2026-10-27", currency="CNY", company_dir=tmp_path, write=True)
    first = api().prepare_workpapers(inputs(), **args)
    target = tmp_path / first["data_relative_path"] / "normalized.json"
    target.write_text("human-edited")
    with pytest.raises(FileExistsError):
        api().prepare_workpapers(inputs(), **args)
    assert target.read_text() == "human-edited"


def test_symlink_in_managed_path_is_not_followed(tmp_path):
    company = tmp_path / "company"; company.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (company / "02_财务数据").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        api().prepare_workpapers(inputs(), ticker="000001.SZ", as_of="2026-10-27",
                                 currency="CNY", company_dir=company, write=True)
    assert list(outside.iterdir()) == []


def test_changed_source_is_a_new_version_without_current_pointer_mutation(tmp_path):
    args = dict(ticker="000001.SZ", as_of="2026-10-27", currency="CNY", company_dir=tmp_path, write=True)
    first = api().prepare_workpapers(inputs(), **args)
    changed = inputs(); changed["income"][0]["revenue"] = 110
    second = api().prepare_workpapers(changed, **args)
    assert first["version_id"] != second["version_id"]
    assert (tmp_path / first["data_relative_path"]).is_dir()
    assert not list(tmp_path.rglob("current*"))


def test_cli_defaults_to_dry_run_with_no_company_writes(tmp_path, capsys):
    source = tmp_path / "source"; source.mkdir()
    company = tmp_path / "company"; company.mkdir()
    for kind, rows in inputs().items(): (source / f"{kind}.json").write_text(json.dumps({"rows": rows}))
    try:
        cli = importlib.import_module("financials.prepare")
    except ModuleNotFoundError:
        pytest.fail("Financial workpaper CLI is not implemented")
    assert cli.main([str(source), str(company), "--ticker", "000001.SZ", "--as-of", "2026-10-27", "--currency", "CNY"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] is False
    assert list(company.iterdir()) == []
