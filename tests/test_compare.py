"""T41: the comparison entry in the workspace (C5; R51, R2, R34).

The underwriter enters their own figure for a memo field or a month. The workspace shows the
difference from the tool's figure, computed in the figures layer (in whole pence for money),
and lists, largest first, the lines in that period whose bucket a person might read
differently: cash deposits, unexplained lines, possible own-account transfers and overridden
lines. Nothing is saved.

Each test talks to a real server running in a thread, through POST /api/compare and
/compare, with the per-launch token."""

import http.client
import json
import random
from datetime import date, timedelta
from decimal import Decimal
from html import escape
from urllib.parse import urlencode

import pytest

from cma import case, metrics, overrides, store
from cma.models import Category
from cma.review import server as rs

RICH = ("id,date,description,amount,balance\n"
        "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,500.00\n"
        "L2,2026-01-06,FPS CREDIT ACME LTD INV 12,300.25,800.25\n"
        "L3,2026-01-07,CASH PAID IN,250.10,1050.35\n"
        "L4,2026-01-08,FPS CREDIT DIRECTOR CAPITAL INTRODUCED,1000.00,2050.35\n"
        "L5,2026-01-09,IWOCA LOAN,2000.00,4050.35\n"
        "L6,2026-01-10,DD IWOCA LTD,-400.00,3650.35\n"
        "L7,2026-01-11,XQZ <b>7</b>,61.00,3711.35\n"
        "L8,2026-01-12,RENT,-2500.00,1211.35\n"
        "L9,2026-02-03,XQZ 4471,80.00,1291.35\n"
        "L10,2026-02-05,CASH PAID IN,3000.00,4291.35\n"
        "L11,2026-02-11,FPS CREDIT BETA LTD INV 77,900.00,5191.35\n"
        "L12,2026-02-20,CASH PAID IN,80.00,5271.35\n")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, accounts=None):
    case_id = store.new_case(label="t41")
    folder = store.case_folder(case_id)
    for account, body in (accounts or {"main": RICH}).items():
        src = tmp_path / f"{account}-{case_id}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    return case_id, folder


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


def _request(srv, method, path, body=None, *, kind="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    try:
        h = {"Host": f"127.0.0.1:{srv.port}", "X-CMA-Token": srv.token}
        data = None
        if body is not None:
            data = json.dumps(body).encode() if kind == "application/json" else body.encode()
            h["Content-Type"] = kind
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        conn.close()


def _compare(srv, **body):
    status, kind, raw = _request(srv, "POST", "/api/compare", body)
    assert kind == "application/json", raw
    return status, json.loads(raw)


def _pence(v: float) -> int:
    return int(Decimal(repr(v)) * 100)


def _figures(folder, account="main"):
    return overrides.case_figures(folder)[account]


def _expected(f, first, last, pairs=()):
    """The lines R51 lists, from the categorised statement: an oracle apart from the build."""
    paired = {i for g in pairs for i in g}
    rows = []
    for n, t in enumerate(f.statement.transactions):
        if not first <= t.date <= last:
            continue
        why = [r for r, ok in (("cash_deposit", t.category == Category.CASH_DEPOSIT),
                               ("unexplained", t.amount > 0 and t.category == Category.OTHER),
                               ("possible_own_account_transfer", t.id in paired),
                               ("overridden", t.id in f.overridden)) if ok]
        if why:
            rows.append((-abs(_pence(t.amount)), n, t.id, why))
    return [(i, why) for *_, i, why in sorted(rows)]


def _entries(folder):
    return [e.event for e in case.for_case(folder).entries()]


# --- a memo field: the difference in pence, and the period's lines largest first ----------


@pytest.mark.contracts
def test_a_memo_field_figure_shows_the_difference_and_the_lines_of_the_period(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    f = _figures(folder)
    m2 = next(m for m in f.metrics if m.id == "M2")
    status, d = _compare(srv, field="monthly_turnover", figure="M2", value="£5,000.37")
    assert status == 200 and d["state"] == "shown"
    assert d["target"] == {"field": "monthly_turnover", "figure": "M2"} and d["unit"] == "GBP"
    assert d["difference_pence"] == 500037 - _pence(m2.value)  # yours less the tool's, in pence (R2)
    assert d["given_display"] == "£5,000.37" and d["ours_display"] == metrics.money_text(_pence(m2.value))
    assert d["difference_display"] == metrics.money_text(500037 - _pence(m2.value))
    assert d["period"] == {"start": "2026-01-05", "end": "2026-02-20"}
    got = [(x["id"], x["reasons"]) for x in d["lines"]]
    assert got == _expected(f, date(2026, 1, 5), date(2026, 2, 20))
    # The fixture's lines R51 names: the cash deposits and the unexplained credits, largest first.
    assert [i for i, _ in got] == ["L10", "L3", "L9", "L12", "L7"]
    assert dict(got)["L10"] == ["cash_deposit"] and dict(got)["L9"] == ["unexplained"]
    amounts = [abs(_pence(x["amount"])) for x in d["lines"]]
    assert amounts == sorted(amounts, reverse=True)


@pytest.mark.contracts
def test_a_month_cell_is_compared_on_the_month_and_lists_only_its_lines(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    f = _figures(folder)
    row = next(r for r in f.monthly if r.month == "2026-02")
    status, d = _compare(srv, month="2026-02", cell="trading_receipts", value="4000")
    assert status == 200
    assert d["difference_pence"] == 400000 - _pence(row.trading_receipts.value)
    assert d["period"] == {"start": row.start.isoformat(), "end": row.end.isoformat()}
    got = [(x["id"], x["reasons"]) for x in d["lines"]]
    assert got == _expected(f, row.start, row.end) and [i for i, _ in got] == ["L10", "L9", "L12"]
    # The page's one select names the same target.
    status, d2 = _compare(srv, target="month:2026-02:trading_receipts", value="4000")
    assert status == 200 and d2["lines"] == d["lines"] and d2["difference_pence"] == d["difference_pence"]


@pytest.mark.contracts
@pytest.mark.parametrize("seed", range(6))
def test_the_difference_is_exact_to_the_penny_for_any_figure_typed(home, tmp_path, serve, seed):
    rng = random.Random(seed)
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    f = _figures(folder)
    by_id = {m.id: m for m in f.metrics}
    for fid in ("M2", "M17", "M18", "M25"):
        p = rng.randrange(-10**13, 10**13)
        typed = metrics.pounds_text(p)
        field = "monthly_turnover"
        status, d = _compare(srv, field=field, figure=fid, value=typed)
        assert status == 200 and d["difference_pence"] == p - _pence(by_id[fid].value)


@pytest.mark.contracts
def test_an_overridden_line_is_listed_as_overridden(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    position = len(case.for_case(folder).entries())
    status, _, raw = _request(srv, "POST", "/api/override", {
        "account": "main", "line": "L11", "category": "director_funding", "reason": "director's money",
        "name": "Pat Jones", "expected": position})
    assert status == 200, raw
    f = _figures(folder)
    assert "L11" in f.overridden
    status, d = _compare(srv, month="2026-02", cell="money_in", value="4060")
    assert status == 200
    got = dict((x["id"], x["reasons"]) for x in d["lines"])
    assert got["L11"] == ["overridden"]
    assert [(x["id"], x["reasons"]) for x in d["lines"]] == _expected(f, date(2026, 2, 1), date(2026, 2, 28))


def _csv(lines, opening=1000_00):
    out, bal = ["id,date,description,amount,balance"], opening
    for i, d, desc, p in lines:
        bal += p
        out.append(f"{i},{d.isoformat()},{desc},{p / 100:.2f},{bal / 100:.2f}")
    return "\n".join(out) + "\n"


@pytest.mark.contracts
def test_possible_own_account_transfers_are_listed_for_each_account_and_across_the_case(home, tmp_path, serve):
    d0 = date(2026, 3, 2)
    main = _csv([("M1", d0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", d0 + timedelta(days=1), "FPS TO 12345678", -100_00)])
    res = _csv([("R1", d0, "CARD SETTLEMENT STRIPE", 321_45), ("R2", d0 + timedelta(days=1), "FPS TO 99998888", -100_00)])
    sav = _csv([("S1", d0 + timedelta(days=2), "FPS FROM 87654321", 100_00)])
    case_id, folder = _case(tmp_path, {"main": main, "reserve": res, "savings": sav})
    srv = serve(case_id)
    figures = overrides.case_figures(folder)
    assert figures["main"].possible_pairs
    status, d = _compare(srv, account="main", field="monthly_turnover", figure="M2", value="500")
    assert status == 200 and d["difference_pence"] == 0
    assert [(x["id"], x["reasons"]) for x in d["lines"]] == [("M2", ["possible_own_account_transfer"])]
    status, d = _compare(srv, account="savings", month="2026-03", cell="money_in", value="0")
    assert status == 200 and d["difference_pence"] == -100_00
    # S1 is an unexplained credit as well as a possible own-account transfer.
    assert [(x["id"], x["reasons"]) for x in d["lines"]] == [("S1", ["unexplained", "possible_own_account_transfer"])]
    status, d = _compare(srv, account=overrides.COMBINED, field="monthly_turnover", figure="M17", value="921.45")
    assert status == 200
    listed = {x["id"]: x["reasons"] for x in d["lines"]}
    assert {k for k, v in listed.items() if "possible_own_account_transfer" in v} == {"main:M2", "reserve:R2", "savings:S1"}
    status, d = _compare(srv, field="monthly_turnover", figure="M2", value="1")
    assert status == 400 and d["error"] == "Name the account."


# --- other units, n/a, and refusals ----------------------------------------------------------


@pytest.mark.contracts
def test_a_figure_that_is_not_money_is_compared_exactly_and_an_na_figure_has_no_difference(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    by_id = {m.id: m for m in _figures(folder).metrics}
    status, d = _compare(srv, field="monthly_turnover", figure="M26", value="12.5%")
    assert status == 200 and d["unit"] == "pct" and d["difference_pence"] is None
    assert Decimal(d["difference"]) == Decimal("12.5") - Decimal(repr(by_id["M26"].value))
    m28 = by_id["M28"]
    assert m28.value is None  # no proposed repayment in the profile: M28 is n/a (R13)
    status, d = _compare(srv, field="affordability", figure="M28", value="1.5x")
    assert status == 200 and d["ours_display"] == "n/a" and d["difference"] is None
    assert d["difference_display"] == "n/a" and d["note"] == m28.method


@pytest.mark.contracts
@pytest.mark.parametrize("body, error", [
    ({"field": "monthly_turnover", "figure": "M2", "value": "100.001"},
     "Money is compared to the penny: write at most two decimals."),
    ({"field": "monthly_turnover", "figure": "M2", "value": "lots"}, "Write the figure as a number, such as 55,000 or 55000.00."),
    ({"field": "monthly_turnover", "figure": "M2", "value": "10000000000000"},
     "An amount of £10,000,000,000,000 or more cannot be compared to the penny."),
    ({"field": "monthly_turnover", "figure": "M2", "value": 1.5}, "Give value as text."),
    ({"field": "monthly_turnover", "value": "1"}, "Name the figure of this memo field."),
    ({"field": "monthly_turnover", "figure": "M8", "value": "1"}, "That figure is not mapped to this memo field."),
    ({"field": "key_risks", "value": "1"}, "No figure is mapped to this memo field."),
    ({"field": "nope", "value": "1"}, "No such memo field."),
    ({"month": "2030-01", "cell": "money_in", "value": "1"}, "No such monthly cell."),
    ({"month": "2026-01", "cell": "nope", "value": "1"}, "No such monthly cell."),
    ({"field": "monthly_turnover", "month": "2026-01", "cell": "money_in", "value": "1"},
     "Name a memo field, or a month and a cell."),
    ({"field": "monthly_turnover", "figure": "M2"}, "Enter your figure."),
    ({"field": "monthly_turnover", "figure": "M2", "value": "1", "extra": "x"},
     "Give only the account, the memo field or month and cell, and your figure."),
])
def test_a_comparison_that_cannot_be_made_is_refused_naming_only_what_was_asked(home, tmp_path, serve, body, error):
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    status, d = _compare(srv, **body)
    assert status == 400 and d == {"error": error}


@pytest.mark.contracts
def test_a_comparison_writes_nothing_to_the_case(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    before = (_entries(folder), sorted(p.name for p in folder.rglob("*")))
    for _ in range(3):
        assert _compare(srv, field="monthly_turnover", figure="M2", value="1")[0] == 200
    assert (_entries(folder), sorted(p.name for p in folder.rglob("*"))) == before


@pytest.mark.contracts
def test_a_case_whose_figures_are_refused_shows_no_comparison(home, tmp_path, serve, monkeypatch):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    (folder / "statements" / "main.csv").write_text("not,a,statement\n", encoding="utf-8")
    status, d = _compare(srv, field="monthly_turnover", figure="M2", value="1")
    assert status == 200 and d["state"] != "shown" and "lines" not in d and "difference" not in d


# --- the page: the entry form, and the comparison rendered with every case text escaped ------


@pytest.mark.contracts
def test_the_case_page_offers_the_comparison_entry(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    status, _, raw = _request(srv, "GET", "/")
    page = raw.decode()
    assert status == 200 and '<section id="compare">' in page
    assert 'value="field:monthly_turnover:M2"' in page and 'value="month:2026-02:trading_receipts"' in page
    assert f'action="/compare?{escape(urlencode({"token": srv.token}))}"' in page


@pytest.mark.contracts
def test_the_comparison_page_escapes_every_case_text(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    body = urlencode({"target": "field:monthly_turnover:M25", "value": "100"})
    status, kind, raw = _request(srv, "POST", "/compare", body, kind="application/x-www-form-urlencoded")
    page = raw.decode()
    assert status == 200 and kind.startswith("text/html")
    assert '<section id="comparison">' in page and "Difference (yours less the tool's)" in page
    assert "XQZ &lt;b&gt;7&lt;/b&gt;" in page and "<b>7</b>" not in page
    status, _, raw = _request(srv, "POST", "/compare", urlencode({"target": "field:nope:M2", "value": "1"}),
                              kind="application/x-www-form-urlencoded")
    assert status == 400 and "No such memo field." in raw.decode()
