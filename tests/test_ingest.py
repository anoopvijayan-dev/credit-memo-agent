"""Input validation (panel run 01, C5: 6/6 seats; requirement R9)."""

from datetime import date

import pytest

from cma import ingest, synth

HEADER = "id,date,description,amount,balance\n"


def _write(tmp_path, body, header=HEADER):
    p = tmp_path / "s.csv"
    p.write_text(header + body)
    return p


def test_valid_synthetic_statement_loads(tmp_path):
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    s = ingest.load_csv(csv_path)
    assert len(s.transactions) == len(rows)


def test_filename_is_not_sent_as_account_name(tmp_path):
    # Security/staff minor: the file stem reached the model prompt.
    p = _write(tmp_path, "T1,2026-01-01,CARD ACQUIRER SETTLEMENT,100,100\n")
    assert ingest.load_csv(p).account_name == "Applicant"


@pytest.mark.parametrize(
    "body, expected",
    [
        ("T1,2026-01-01,A,100,100\nT1,2026-01-02,B,100,200\n", "duplicate id T1"),
        ("T1,2026-01-01,A,100,100\nT2,2026-01-02,B,100,999999\n", "running balance"),
        (",2026-01-01,A,100,100\n", "empty id"),
        ("T1,2026-01-01,A,nan,100\n", "amount 'nan'"),
        ("T1,2026-01-01,A,100,inf\n", "balance 'inf'"),
        ("T1,01/02/2026,A,100,100\n", "not YYYY-MM-DD"),
        ("T1,2026-01-01,A,ten,100\n", "amount 'ten'"),
    ],
)
def test_bad_rows_are_refused(tmp_path, body, expected):
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, body))
    assert expected in str(e.value)


def test_missing_column_is_named(tmp_path):
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, "T1,2026-01-01,A,100\n", header="id,date,description,amount\n"))
    assert "missing column(s): balance" in str(e.value)


def test_empty_file_is_refused(tmp_path):
    with pytest.raises(ingest.StatementError):
        ingest.load_csv(_write(tmp_path, ""))


def test_all_problems_are_reported_with_line_numbers(tmp_path):
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, "T1,2026-01-01,A,100,100\nT1,2026-01-02,B,x,200\n"))
    assert len(e.value.problems) == 2
    assert all(p.startswith("line 3") for p in e.value.problems)
