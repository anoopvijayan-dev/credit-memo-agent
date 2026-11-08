"""MCP tools read only statements and policies under the data directory (panel run 02, D19)."""

from datetime import date

import pytest

pytest.importorskip("mcp")

from cma import mcp_server, synth


@pytest.fixture
def data(tmp_path, monkeypatch):
    rows, truth = synth.generate("cafe", "good", end=date(2026, 9, 30))
    synth.write(rows, truth, tmp_path / "data", stem="cafe")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_reads_statements_inside_the_data_dir(data):
    assert mcp_server.statement_summary("cafe.csv")["transactions"] > 100
    assert any(m["id"] == "M16" for m in mcp_server.compute_metrics("cafe.csv", vat_stagger=3))


@pytest.mark.parametrize("path", ["../outside.csv", "/etc/passwd", "cafe.truth.json", "missing.csv"])
def test_refuses_other_paths(data, path):
    (data / "outside.csv").write_text("x")
    with pytest.raises(ValueError):
        mcp_server.statement_summary(path)


def test_policy_path_is_checked_too(data):
    with pytest.raises(ValueError):
        mcp_server.check_policy("cafe.csv", policy_path="/etc/hosts")
