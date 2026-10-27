import importlib

import pytest


def api():
    try:
        return importlib.import_module("research.inventory")
    except ModuleNotFoundError:
        pytest.fail("Company source inventory is not implemented")


def test_inventory_is_metadata_only_and_excludes_generated_outputs_and_symlinks(tmp_path):
    (tmp_path / "old-model.xlsx").write_bytes(b"not read or changed")
    (tmp_path / "research.pdf").write_bytes(b"not read")
    generated = tmp_path / "02_财务数据"; generated.mkdir()
    (generated / "output.json").write_text("generated")
    (tmp_path / "alias.xlsx").symlink_to(tmp_path / "old-model.xlsx")
    result = api().scan_company(tmp_path)
    assert sorted(x["relative_path"] for x in result["files"]) == ["old-model.xlsx", "research.pdf"]
    assert all(x["content_inspected"] is False for x in result["files"])
    assert all(not x["relative_path"].startswith("/") for x in result["files"])
    assert (tmp_path / "old-model.xlsx").read_bytes() == b"not read or changed"


def test_inventory_limit_is_visible_instead_of_claiming_complete_coverage(tmp_path):
    for i in range(3): (tmp_path / f"{i}.pdf").write_bytes(b"x")
    result = api().scan_company(tmp_path, max_files=2)
    assert len(result["files"]) == 2
    assert result["truncated"] is True
