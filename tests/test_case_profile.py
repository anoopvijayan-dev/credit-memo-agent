"""T05: the case profile in the case pipeline (R5, R6, R13).

A case's profile reaches the categoriser and the figures layer whenever the case's figures
are computed (overrides.case_figures), and the workspace shows the result (C5):

- a director's or shareholder's name makes a credit director and shareholder funding (M20),
  even with an invoice reference, unless a protected verdict decides it first (R5);
- an own account's name makes a line an own-account transfer in either direction, out of
  trading receipts (M2) and out of net operating cash flow (M9) (R6);
- a credit both kinds of name match, with no protected verdict, is ambiguous: unexplained
  money in (M25), with every rule it matched and both profile kinds as candidates (R5, R6),
  under v1 as under v2 (R54);
- the proposed monthly repayment feeds debt service cover including the proposed facility
  (M28); with none, M28 is n/a with the reason (R13).

Each test goes through the workspace API (/api/case) of a real server in a thread, on cases
with and without each profile field."""

import http.client
import json
from decimal import Decimal

import pytest

from cma import overrides, store
from cma.models import Category
from cma.review import server as rs

# Opening balance £1,000.00.
BODY = ("id,date,description,amount,balance\n"
        "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
        "L2,2026-01-06,FPS CREDIT JANE DOE INV 42,1000.00,2500.00\n"
        "L3,2026-01-07,FPS CREDIT ACME HOLDINGS,200.00,2700.00\n"
        "L4,2026-01-08,FPS TO ACME HOLDINGS,-300.00,2400.00\n"
        "L5,2026-01-09,DD IWOCA LOAN,-100.00,2300.00\n"
        "L6,2026-01-10,FPS CREDIT JOHN ROE,50.00,2350.00\n"
        "L7,2026-01-11,FPS CREDIT JANE DOE ACME HOLDINGS,70.00,2420.00\n"
        "L8,2026-01-12,IWOCA JANE DOE ACME HOLDINGS,80.00,2500.00\n"
        "L9,2026-01-13,REFUND JANE DOE,10.00,2510.00\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


@pytest.fixture
def serve():
    servers = []

    def start(case_id):
        srv = rs.start(store.case_folder(case_id), case_id, port=0)
        srv.serve_in_thread()
        servers.append(srv)
        return srv

    yield start
    for srv in servers:
        srv.close()


def _case(tmp_path, **profile):
    case_id = store.new_case(label="t05")
    folder = store.case_folder(case_id)
    if profile:
        store.update_profile(folder, lambda p: {**p, **profile})
    src = tmp_path / "main.csv"
    src.write_text(BODY, encoding="utf-8")
    store.import_file(folder, src, "csv", account="main")
    return case_id, folder


def _get(srv, path):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{srv.port}", "X-CMA-Token": srv.token})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def _api(srv) -> dict:
    status, body = _get(srv, "/api/case")
    assert status == 200
    return json.loads(body)


def _figures(summary) -> dict:
    assert summary["state"] == "shown", summary
    (account,) = summary["accounts"]
    return {f["id"]: f for f in account["figures"]}


def _pounds(f) -> Decimal:
    return Decimal(f["display"].replace("£", "").replace(",", ""))


def _lines(folder) -> dict:
    (figs,) = overrides.case_figures(folder).values()
    return {t.id: t for t in figs.statement.transactions}, {m.id: m for m in figs.metrics}


def _shown(home, tmp_path, serve, **profile):
    case_id, folder = _case(tmp_path, **profile)
    summary = _api(serve(case_id))
    return summary, _figures(summary), folder


@pytest.mark.contracts
def test_without_a_profile_no_line_is_director_funding_or_an_own_account_transfer(home, tmp_path, serve):
    summary, f, folder = _shown(home, tmp_path, serve)
    assert _pounds(f["M20"]) == 0 and _pounds(f["M19"]) == 0
    assert _pounds(f["M2"]) == Decimal("1500.00")  # the invoice-referenced credit is trading receipts
    lines, _ = _lines(folder)
    assert lines["L2"].category == Category.REVENUE_TRANSFER
    assert summary["profile"] == {"directors": [], "shareholders": [], "own_accounts": [],
                                  "proposed_monthly_repayment": None}


@pytest.mark.contracts
def test_a_director_s_name_makes_a_credit_director_funding_even_with_an_invoice_reference(home, tmp_path, serve):
    summary, f, folder = _shown(home, tmp_path, serve, directors=["Jane Doe"])
    lines, _ = _lines(folder)
    assert (lines["L2"].category, lines["L2"].rule_id) == (Category.DIRECTOR_FUNDING, "profile:director")
    assert (lines["L7"].category, lines["L7"].rule_id) == (Category.DIRECTOR_FUNDING, "profile:director")
    # A protected verdict decides first: a lending drawdown and a refund (R5).
    assert lines["L8"].category == Category.LOAN_DRAWDOWN
    assert lines["L9"].category == Category.REFUND_IN
    assert _pounds(f["M20"]) == Decimal("1070.00")
    assert _pounds(f["M2"]) == Decimal("500.00")
    assert f["M20"]["lines"] == 2
    assert summary["profile"]["directors"] == ["Jane Doe"]


@pytest.mark.contracts
def test_a_shareholder_s_name_makes_a_credit_director_funding(home, tmp_path, serve):
    summary, f, folder = _shown(home, tmp_path, serve, shareholders=["John Roe"])
    lines, _ = _lines(folder)
    assert (lines["L6"].category, lines["L6"].rule_id) == (Category.DIRECTOR_FUNDING, "profile:director")
    assert _pounds(f["M20"]) == Decimal("50.00")
    assert lines["L2"].category == Category.REVENUE_TRANSFER  # no director named
    assert summary["profile"]["shareholders"] == ["John Roe"]


@pytest.mark.contracts
def test_an_own_account_s_name_makes_lines_own_account_transfers_out_of_m2_and_m9(home, tmp_path, serve):
    summary, f, folder = _shown(home, tmp_path, serve, own_accounts=["ACME HOLDINGS"])
    lines, metrics = _lines(folder)
    for line_id in ("L3", "L4", "L7"):  # credits and the debit alike (R6)
        assert (lines[line_id].category, lines[line_id].rule_id) == (Category.OWN_ACCOUNT_TRANSFER,
                                                                      "profile:own_account"), line_id
    assert lines["L8"].category == Category.LOAN_DRAWDOWN  # protected verdict first (R6)
    assert _pounds(f["M19"]) == Decimal("270.00")
    assert _pounds(f["M2"]) == Decimal("1500.00")
    # Neither the own-account credit nor the own-account debit is operating cash flow.
    assert not {"L3", "L4", "L7"} & set(metrics["M9"].evidence)
    assert not {"L3", "L4", "L7"} & set(metrics["M2"].evidence)
    assert _pounds(f["M9"]) == Decimal("1500.00")
    assert summary["profile"]["own_accounts"] == ["ACME HOLDINGS"]


@pytest.mark.contracts
def test_without_the_own_account_named_the_debit_to_it_stays_in_m9(home, tmp_path, serve):
    _, f, folder = _shown(home, tmp_path, serve)
    _, metrics = _lines(folder)
    assert "L4" in metrics["M9"].evidence
    assert _pounds(f["M9"]) != Decimal("1500.00")


@pytest.mark.contracts
def test_a_credit_both_kinds_name_is_ambiguous_with_both_candidates(home, tmp_path, serve):
    summary, f, folder = _shown(home, tmp_path, serve, directors=["Jane Doe"], own_accounts=["ACME HOLDINGS"])
    lines, _ = _lines(folder)
    l7 = lines["L7"]
    assert l7.category == Category.OTHER
    assert l7.rule_id.startswith("v2:conflict:")
    assert l7.rule_id.endswith("+profile:director+profile:own_account")
    assert lines["L8"].category == Category.LOAN_DRAWDOWN  # IWOCA JANE DOE ACME HOLDINGS (R5, R6)
    assert lines["L2"].category == Category.DIRECTOR_FUNDING
    assert _pounds(f["M25"]) == Decimal("120.00")  # L6 (no shareholder named) and L7
    assert _pounds(f["M20"]) == Decimal("1000.00")
    assert _pounds(f["M19"]) == Decimal("200.00")
    assert summary["profile"]["directors"] == ["Jane Doe"] and summary["profile"]["own_accounts"] == ["ACME HOLDINGS"]


@pytest.mark.contracts
def test_without_a_proposed_repayment_m28_is_n_a_with_the_reason(home, tmp_path, serve):
    _, f, _ = _shown(home, tmp_path, serve)
    assert f["M28"]["value"] is None and f["M28"]["display"] == "n/a"
    assert "proposed monthly repayment" in f["M28"]["method"]


@pytest.mark.contracts
def test_the_proposed_repayment_feeds_m28(home, tmp_path, serve):
    summary, f, folder = _shown(home, tmp_path, serve, proposed_monthly_repayment="250.00")
    assert f["M28"]["value"] is not None
    assert f["M28"]["value"] < f["M10"]["value"]  # the proposed repayment adds to debt service
    assert summary["profile"]["proposed_monthly_repayment"] == "250.00"
    _, metrics = _lines(folder)
    assert metrics["M28"].evidence


@pytest.mark.contracts
def test_the_page_shows_the_profile_escaped(home, tmp_path, serve):
    case_id, _ = _case(tmp_path, directors=["<b>Jane</b> & Co"], own_accounts=["ACME HOLDINGS"],
                       proposed_monthly_repayment="250.00")
    srv = serve(case_id)
    status, body = _get(srv, "/")
    page = body.decode("utf-8")
    assert status == 200
    assert "&lt;b&gt;Jane&lt;/b&gt; &amp; Co" in page and "<b>Jane</b>" not in page
    assert "ACME HOLDINGS" in page and "£250.00" in page


@pytest.mark.contracts
def test_the_page_says_when_no_profile_field_is_given(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    status, body = _get(serve(case_id), "/")
    page = body.decode("utf-8")
    assert status == 200
    assert 'id="profile"' in page and "none named" in page and "No proposed monthly repayment" in page


@pytest.mark.contracts
def test_a_both_kinds_credit_is_ambiguous_under_v1_too(home, tmp_path, serve, monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v1")
    _, f, folder = _shown(home, tmp_path, serve, directors=["Jane Doe"], own_accounts=["ACME HOLDINGS"])
    lines, _ = _lines(folder)
    assert lines["L7"].category == Category.OTHER
    assert lines["L7"].rule_id.startswith("v1:conflict:")
    assert lines["L7"].rule_id.endswith("profile:director+profile:own_account")
    assert lines["L3"].category == Category.OWN_ACCOUNT_TRANSFER
    assert lines["L2"].category == Category.DIRECTOR_FUNDING
    assert _pounds(f["M25"]) >= Decimal("70.00") and _pounds(f["M19"]) == Decimal("200.00")


@pytest.mark.contracts
def test_a_single_profile_name_decides_under_v1(home, tmp_path, serve, monkeypatch):
    monkeypatch.setenv("CMA_RULESET", "v1")
    _, f, folder = _shown(home, tmp_path, serve, directors=["Jane Doe"])
    lines, _ = _lines(folder)
    assert (lines["L7"].category, lines["L7"].rule_id) == (Category.DIRECTOR_FUNDING, "profile:director")
    # v1's protected rules come first, and v1 protects neither IWOCA nor REFUND wording on a
    # credit, so the director's name takes L8 and L9 too (C1, profile names under v1).
    assert _pounds(f["M20"]) == Decimal("1160.00")
