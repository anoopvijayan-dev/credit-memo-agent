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


# --- panel run 03 ------------------------------------------------------------------

def test_no_data_dir_means_no_reads(tmp_path, monkeypatch):
    # E12: the root defaulted to the working directory, often / for MCP hosts.
    monkeypatch.delenv("CMA_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.csv").write_text("id,date,description,amount,balance\n")
    with pytest.raises(ValueError, match="CMA_DATA_DIR is not set"):
        mcp_server.statement_summary("x.csv")


def test_refused_statement_does_not_echo_cell_values(data):
    (data / "data" / "bad.csv").write_text("id,date,description,amount,balance\nT1,2026-01-01,A,SECRET-VALUE,1\n")
    with pytest.raises(ValueError) as e:
        mcp_server.statement_summary("bad.csv")
    assert "SECRET-VALUE" not in str(e.value) and "line 2" in str(e.value)


def test_descriptions_only_on_request_and_marked_untrusted(data):
    rows = mcp_server.transactions_for("cafe.csv", ["T00001"])
    assert "description" not in rows[0] and "untrusted_description" not in rows[0]
    rows = mcp_server.transactions_for("cafe.csv", ["T00001"], include_descriptions=True)
    assert rows[0]["untrusted_description"]


def test_a_refusal_says_which_flow_reached_the_limit_however_many_lines_are_wrong(data):
    """T04 review 12: a statement refused because its money in reaches R2's limit says so
    through MCP, even when more than ten lines are also named; no cell value is echoed."""
    rows = ["id,date,description,amount,balance",
            "T1,2026-01-01,A,9999999999999.99,9999999999999.99",
            "T2,2026-01-02,B,9999999999999.99,9999999999999.99"]
    rows += [f"X{i},SECRET-DATE,C,1.00,1.00" for i in range(12)]
    (data / "data" / "big.csv").write_text("\n".join(rows) + "\n")
    with pytest.raises(ValueError) as e:
        mcp_server.statement_summary("big.csv")
    msg = str(e.value)
    assert "money in totals £10,000,000,000,000 or more" in msg and "money out" not in msg
    assert "SECRET-DATE" not in msg and "line 3" in msg
