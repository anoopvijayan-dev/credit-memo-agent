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


# --- panel run 02, D13 -------------------------------------------------------------

def test_typo_year_out_of_order_is_refused(tmp_path):
    body = "T1,2026-01-01,A,100,100\nT2,2062-01-02,B,100,200\nT3,2026-01-03,C,100,300\n"
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, body), today=date(2070, 1, 1))
    assert "out of order" in str(e.value)


def test_future_date_is_refused(tmp_path):
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, "T1,2026-01-01,A,100,100\nT2,2062-01-02,B,100,200\n"), today=date(2026, 11, 8))
    assert "in the future" in str(e.value)


def test_newest_first_file_is_read_oldest_first(tmp_path):
    body = "T3,2026-01-03,C,-50,150\nT2,2026-01-02,B,100,200\nT1,2026-01-01,A,100,100\n"
    s = ingest.load_csv(_write(tmp_path, body))
    assert [t.id for t in s.transactions] == ["T1", "T2", "T3"]


def test_byte_order_mark_is_tolerated(tmp_path):
    p = tmp_path / "bom.csv"
    p.write_bytes(("﻿" + HEADER + "T1,2026-01-01,A,100,100\n").encode())
    assert len(ingest.load_csv(p).transactions) == 1


@pytest.mark.parametrize("amount", ["1e3", "1,000", "£100", "100.123", " ", "0x10"])
def test_amounts_must_be_plain(tmp_path, amount):
    with pytest.raises(ingest.StatementError):
        ingest.load_csv(_write(tmp_path, f'T1,2026-01-01,A,"{amount}",100\n'))


def test_extra_fields_are_refused(tmp_path):
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, "T1,2026-01-01,A,100,100,surprise\n"))
    assert "more fields" in str(e.value)


def test_split_and_monzo_exports_get_a_pointer(tmp_path):
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, "1,2,3\n", header="Date,Paid in,Paid out\n"))
    assert "one signed amount column" in str(e.value)
    with pytest.raises(ingest.StatementError) as e:
        ingest.load_csv(_write(tmp_path, "1,2,3\n", header="Transaction ID,Date,Money Out\n"))
    assert "cma import monzo" in str(e.value)
