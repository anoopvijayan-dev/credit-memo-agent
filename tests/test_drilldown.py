"""T20: the workspace drill-down (C5; R16, P2, R34).

Selecting a figure, a money-in bucket or a monthly cell lists exactly the lines behind it,
each with date, description, amount, balance, category and the rule or override that set
the category. Beside them: for a sum figure the total of the lines' signed amounts, which is
the figure; for an outflow figure that total, which is the figure negated; for a derived
figure its inputs and method, and for M9 the lines it counts only in part.

Each test talks to a real server running in a thread, through /api/lines and /lines, with the
per-launch token."""

import csv
import http.client
import io
import json
import random
from decimal import Decimal
from html import escape
from urllib.parse import urlencode

import pytest

from cma import metrics, overrides, store
from cma.review import server as rs
from cma.review import summary

RICH = ("id,date,description,amount,balance\n"
        "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,500.00\n"
        "L2,2026-01-06,FPS CREDIT ACME LTD INV 12,300.25,800.25\n"
        "L3,2026-01-07,CASH PAID IN,250.10,1050.35\n"
        "L4,2026-01-08,FPS CREDIT DIRECTOR CAPITAL INTRODUCED,1000.00,2050.35\n"
        "L5,2026-01-09,IWOCA LOAN,2000.00,4050.35\n"
        "L6,2026-01-10,DD IWOCA LTD,-400.00,3650.35\n"
        "L7,2026-01-11,HMRC VAT,-700.00,2950.35\n"
        "L8,2026-01-12,HMRC PAYE,-300.00,2650.35\n"
        "L9,2026-01-13,DIVIDEND J SMITH,-900.00,1750.35\n"
        "L10,2026-01-14,FPS DEBIT J BLOGGS,-100.00,1650.35\n"
        "L11,2026-01-15,FPS CREDIT J BLOGGS,250.00,1900.35\n"
        "L12,2026-01-20,RENT,-2500.00,-599.65\n"
        "L13,2026-02-03,XQZ 4471,80.00,-519.65\n"
        "L14,2026-02-10,PAYPAL REFUND,20.00,-499.65\n"
        "L15,2026-02-11,FPS CREDIT BETA LTD INV 77,900.00,400.35\n"
        "L16,2026-02-12,UNPAID DD IWOCA LTD,400.00,800.35\n"
        "L17,2026-02-20,DD IWOCA LTD,-400.00,400.35\n")
SUMS = ["M2", "M17", "M18", "M19", "M20", "M21", "M22", "M23", "M24", "M25"]
OUTFLOWS = ["M8", "M11", "M12", "M14"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, body=RICH, *, account="main", case_id=None, repayment="250.00"):
    if case_id is None:
        case_id = store.new_case(label="t20")
    folder = store.case_folder(case_id)
    if repayment is not None:
        store.update_profile(folder, lambda p: {**p, "proposed_monthly_repayment": repayment})
    src = tmp_path / f"{account}.csv"
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


def _get(srv, path):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{srv.port}", "X-CMA-Token": srv.token})
        r = conn.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        conn.close()


def _lines(srv, **target) -> dict:
    status, kind, body = _get(srv, "/api/lines?" + urlencode(target))
    assert status == 200 and kind == "application/json", body
    return json.loads(body)


def _pence(display: str) -> int:
    return int(Decimal(display.replace("£", "").replace(",", "")) * 100)


def _figure_pence(value: float) -> int:
    return int(Decimal(repr(value)) * 100)


def _check_line(line: dict, t) -> None:
    """Each line carries what R16 lists, as the categorised statement has it."""
    assert line["id"] == t.id and line["date"] == t.date.isoformat() and line["description"] == t.description
    assert line["amount"] == t.amount and line["balance"] == t.balance
    assert _pence(line["amount_display"]) == _figure_pence(t.amount)
    assert _pence(line["balance_display"]) == _figure_pence(t.balance)
    assert line["category"] == t.category.value and line["rule"] == t.rule_id and line["rule"]


# --- every figure and every cell: exactly its lines, and its total by kind -----------------


@pytest.mark.invariants
def test_p2_every_figure_s_drilldown_lists_exactly_its_lines_and_total_by_kind(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    f = overrides.case_figures(folder)["main"]
    by_line = f.statement.by_id()
    kinds = set()
    for m in f.metrics:
        d = _lines(srv, figure=m.id)
        assert d["state"] == "shown" and d["id"] == m.id and d["account"] == "main"
        assert d["value"] == m.value and d["display"] == m.display_exact() and d["method"] == m.method
        # Exactly the figure's evidence: the same lines, in the same order, no more, no fewer.
        assert [x["id"] for x in d["lines"]] == m.evidence
        for x in d["lines"]:
            _check_line(x, by_line[x["id"]])
        kind = metrics.figure_kind(m.id)
        kinds.add(kind)
        assert d["kind"] == kind
        if kind == "sum":
            assert m.id in SUMS
            assert d["total"]["pence"] == _figure_pence(m.value) == _pence(d["total"]["display"])
            assert d["total"]["is"] == "the figure" and d["inputs"] is None
        elif kind == "outflow":
            assert m.id in OUTFLOWS
            assert d["total"]["pence"] == -_figure_pence(m.value) == _pence(d["total"]["display"])
            assert d["total"]["is"].startswith("the figure negated") and d["inputs"] is None
        else:
            assert d["total"] is None and isinstance(d["inputs"], list) and d["method"]
    assert kinds == {"sum", "outflow", "derived"}


@pytest.mark.invariants
def test_p2_every_monthly_cell_s_drilldown_lists_exactly_its_lines_and_total_by_kind(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    f = overrides.case_figures(folder)["main"]
    seen = set()
    for row in f.monthly:
        for key, _ in summary.CELLS:
            c = getattr(row, key)
            d = _lines(srv, month=row.month, cell=key)
            assert d["target"] == {"month": row.month, "cell": key} and d["kind"] == c.kind
            assert [x["id"] for x in d["lines"]] == c.evidence
            for x in d["lines"]:
                _check_line(x, f.statement.by_id()[x["id"]])
            seen.add(c.kind)
            if c.kind == "sum":
                assert d["total"]["pence"] == _figure_pence(c.value) == _figure_pence(c.total)
            elif c.kind == "outflow":
                assert d["total"]["pence"] == -_figure_pence(c.value) == _figure_pence(c.total)
            else:
                assert d["total"] is None and d["method"]
                # The closes it reads: every day of the month in the period, each set by a cited line.
                days = (row.end - row.start).days + 1
                assert len(d["closes"]) == days and {x["line"] for x in d["closes"]} == set(c.evidence)
    assert seen == {"sum", "outflow", "derived"}


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(20))
def test_p2_drilldown_totals_equal_the_figures_on_random_statements(home, tmp_path, serve, seed):
    rng = random.Random(seed)
    words = ["STRIPE PAYOUT", "CASH PAID IN", "FPS CREDIT ACME LTD INV 12", "IWOCA LOAN", "PAYPAL REFUND",
             "HMRC VAT", "HMRC PAYE", "DIVIDEND J SMITH", "DD IWOCA LTD", "RENT", "XQZ 4471", "FPS CREDIT J BLOGGS",
             "FPS DEBIT J BLOGGS", "INTEREST PAID", "FPS CREDIT DIRECTOR CAPITAL INTRODUCED"]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["id", "date", "description", "amount", "balance"])
    balance = 0
    for n in range(rng.randint(1, 25)):
        p = rng.choice([1, -1]) * rng.randint(1, 900_000)
        balance += p
        w.writerow([f"L{n + 1}", f"2026-0{1 + n // 10}-{1 + n % 10 * 2:02d}", rng.choice(words),
                    f"{p / 100:.2f}", f"{balance / 100:.2f}"])
    case_id, folder = _case(tmp_path, out.getvalue())
    srv = serve(case_id)
    f = overrides.case_figures(folder)["main"]
    for m in f.metrics:
        d = _lines(srv, figure=m.id)
        assert [x["id"] for x in d["lines"]] == m.evidence
        if d["kind"] == "sum":
            assert d["total"]["pence"] == _figure_pence(m.value)
        elif d["kind"] == "outflow":
            assert d["total"]["pence"] == -_figure_pence(m.value)
    for row in f.monthly:
        for key, _ in summary.CELLS:
            d = _lines(srv, month=row.month, cell=key)
            assert [x["id"] for x in d["lines"]] == getattr(row, key).evidence


# --- derived figures: inputs and method ------------------------------------------------------


@pytest.mark.contracts
def test_derived_figures_show_their_inputs_and_method(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    by = {m.id: m for m in overrides.case_figures(folder)["main"].metrics}
    # A ratio: the figures it is computed from.
    m10 = _lines(srv, figure="M10")
    assert [(i["figure"], i["display"]) for i in m10["inputs"]] == [
        ("M9", by["M9"].display_exact()), ("M11", "£900.00"), ("M8", "£400.00")]
    assert m10["method"] == by["M10"].method
    m28 = _lines(srv, figure="M28")
    assert {"label": "Proposed monthly repayment (case profile)", "figure": None, "display": "£250.00"} in m28["inputs"]
    # An average: M2 and the period.
    m3 = _lines(srv, figure="M3")
    assert m3["inputs"][0]["figure"] == "M2" and m3["inputs"][0]["display"] == "£1,950.35"
    assert any(i["label"] == "Period" and "2026-01-05 to 2026-02-20" in i["display"] for i in m3["inputs"])
    # A share: its numerator, with the lines counted in it, and what it is a share of.
    m15 = _lines(srv, figure="M15")
    assert m15["inputs"][0]["display"] == "£900.00" and m15["inputs"][0]["lines"] == ["L15"]
    assert m15["inputs"][1]["display"] == "£1,200.25"
    m26 = _lines(srv, figure="M26")
    assert m26["inputs"][0]["figure"] == "M25" and m26["inputs"][1]["lines"] == ["L11", "L13"]
    assert _pence(m26["inputs"][2]["display"]) == metrics.lines_total(
        overrides.case_figures(folder)["main"].statement, by["M26"].evidence)
    # A lowest balance: the line that set it, and its balance.
    m5 = _lines(srv, figure="M5")
    assert [x["id"] for x in m5["lines"]] == ["L12"] and m5["inputs"][0]["display"] == "-£599.65"
    # Read from end-of-day balances: the close of every day of the period.
    m27 = _lines(srv, figure="M27")
    assert len(m27["closes"]) == 47 and m27["closes"][0] == {"date": "2026-01-05", "display": "£500.00",
                                                             "overdrawn": False, "line": "L1"}
    m6 = _lines(srv, figure="M6")
    assert sum(c["overdrawn"] for c in m6["closes"]) == by["M6"].value == 22


def _months_statement() -> str:
    """Eight whole months, Jan to Aug 2026, with trading receipts that differ month to month,
    a VAT payment in group 1's June window and none in September's, and a returned DD."""
    rows, bal, n = [], 0, 0

    def add(day, desc, amount):
        nonlocal bal, n
        n += 1
        bal += amount
        rows.append(f"L{n},{day},{desc},{amount / 100:.2f},{bal / 100:.2f}")

    for month, takings in zip(range(1, 9), (1000_00, 1100_00, 900_00, 1200_00, 1300_00, 1500_00,
                                            1700_00, 1600_00), strict=True):
        add(f"2026-{month:02d}-01", "CARD SETTLEMENT STRIPE", takings)
        add(f"2026-{month:02d}-15", "DD IWOCA LTD", -100_00)
    add("2026-05-06", "HMRC VAT", -300_00)
    add("2026-08-10", "HMRC VAT", -250_00)
    add("2026-08-11", "UNPAID DD HMRC VAT", 250_00)
    add("2026-08-31", "CARD SETTLEMENT STRIPE", 1_00)
    rows.sort(key=lambda r: r.split(",")[1])
    return "id,date,description,amount,balance\n" + "\n".join(_rebalanced(rows)) + "\n"


def _rebalanced(rows):
    bal = 0
    for r in rows:
        i, d, desc, amount, _ = r.split(",")
        bal += int(Decimal(amount) * 100)
        yield f"{i},{d},{desc},{amount},{bal / 100:.2f}"


@pytest.mark.contracts
def test_every_derived_figure_shows_inputs_that_give_it(home, tmp_path, serve):
    """R16: no derived figure's drill-down is without its inputs (verify-241)."""
    for body in (RICH, _months_statement()):
        case_id, folder = _case(tmp_path, body)
        store.update_profile(folder, lambda p: {**p, "vat_stagger": 1})
        srv = serve(case_id)
        for m in overrides.case_figures(folder)["main"].metrics:
            if metrics.figure_kind(m.id) != "derived":
                continue
            d = _lines(srv, figure=m.id)
            assert d["inputs"] or d["closes"], m.id


@pytest.mark.contracts
def test_m4_shows_the_two_quarters_it_compares(home, tmp_path, serve):
    case_id, folder = _case(tmp_path, _months_statement())
    m4 = next(m for m in overrides.case_figures(folder)["main"].metrics if m.id == "M4")
    d = _lines(serve(case_id), figure="M4")
    first, last = d["inputs"]
    assert "2026-01 to 2026-03" in first["label"] and "2026-06 to 2026-08" in last["label"]
    f, la = _pence(first["display"]), _pence(last["display"])
    assert (f, la) == (3000_00, 4801_00)
    assert round((la - f) / f * 100, 2) == m4.value
    assert f + la == metrics.lines_total(overrides.case_figures(folder)["main"].statement, m4.evidence)
    assert sorted(first["lines"] + last["lines"], key=lambda i: int(i[1:])) == m4.evidence


@pytest.mark.contracts
def test_m4_with_too_few_whole_months_says_how_many_it_has(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    d = _lines(serve(case_id), figure="M4")
    assert d["value"] is None and d["inputs"][0]["display"] == "0 (6 needed)"


@pytest.mark.contracts
def test_m9_shows_its_operating_total_and_the_offset_it_nets(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    m9 = next(m for m in overrides.case_figures(folder)["main"].metrics if m.id == "M9")
    d = _lines(serve(case_id), figure="M9")
    trading, offset, part = d["inputs"]
    assert "L11" not in trading["lines"] and set(trading["lines"]) | {"L11"} == set(m9.evidence)
    assert offset["lines"] == ["L11"] and _pence(offset["display"]) == 100_00
    assert _pence(trading["display"]) + _pence(offset["display"]) == _figure_pence(m9.value)
    assert part["label"] == "Line L11 counts" and part["display"] == "£100.00 of £250.00"


@pytest.mark.contracts
def test_m13_shows_the_stagger_group_and_each_quarter_judged(home, tmp_path, serve):
    case_id, folder = _case(tmp_path, _months_statement())
    store.update_profile(folder, lambda p: {**p, "vat_stagger": 1})
    m13 = next(m for m in overrides.case_figures(folder)["main"].metrics if m.id == "M13")
    d = _lines(serve(case_id), figure="M13")
    group, *quarters = d["inputs"]
    assert group["label"] == "VAT stagger group (case profile)" and group["display"].startswith("1 ")
    assert [q["label"] for q in quarters] == [
        "Quarter ended 2025-12-31: due 2026-02-07, collected by 2026-02-11",
        "Quarter ended 2026-03-31: due 2026-05-07, collected by 2026-05-12",
        "Quarter ended 2026-06-30: due 2026-08-07, collected by 2026-08-12"]
    may = next(x["id"] for x in d["lines"] if x["date"] == "2026-05-06")
    # The June quarter's payment was returned unpaid, so it did not stick.
    assert [q["display"] for q in quarters] == ["late or missed", f"paid by line {may}", "late or missed"]
    assert m13.value == sum(q["display"] == "late or missed" for q in quarters) == 2
    store.update_profile(folder, lambda p: {**p, "vat_stagger": None})
    d = _lines(serve(case_id), figure="M13")
    assert d["inputs"] == [{"label": "VAT stagger group (case profile)", "figure": None, "display": "not given"}]


@pytest.mark.contracts
def test_m7_shows_its_count(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    d = _lines(serve(case_id), figure="M7")
    assert d["inputs"][0]["display"] == str(int(d["value"])) == str(len(d["lines"])) == "1"


@pytest.mark.contracts
def test_m9_names_the_lines_it_counts_only_in_part(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    d = _lines(serve(case_id), figure="M9")
    assert d["kind"] == "derived" and d["total"] is None
    assert d["counted_in_part"] == ["L11"]
    assert [x["id"] for x in d["lines"] if x["counted_in_part"]] == ["L11"]


@pytest.mark.contracts
def test_a_line_s_override_is_the_rule_shown_for_it(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    overrides.add_override(folder, "L13", "director_funding", reason="director's loan", name="A Person")
    srv = serve(case_id)
    d = _lines(srv, figure="M20")
    assert [x["id"] for x in d["lines"]] == ["L4", "L13"] and d["overridden"] is True
    l13 = d["lines"][1]
    assert l13["rule"] == "override:1" and l13["overridden"] is True and l13["category"] == "director_funding"
    assert d["lines"][0]["rule"] == "v2:director_funding" and d["lines"][0]["overridden"] is False
    assert d["total"]["display"] == "£1,080.00" == by_display(folder, "M20")
    assert [x["id"] for x in _lines(srv, figure="M25")["lines"]] == ["L11"]


def by_display(folder, fid):
    return next(m for m in overrides.case_figures(folder)["main"].metrics if m.id == fid).display_exact()


# --- requests that name nothing, and cases that cannot be shown ------------------------------


@pytest.mark.contracts
def test_a_request_that_names_no_figure_or_cell_is_answered_400(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    for q in ({}, {"figure": "M99"}, {"figure": "M2", "month": "2026-01"}, {"month": "2026-01"},
              {"month": "2031-01", "cell": "money_in"}, {"month": "2026-01", "cell": "nope"},
              {"figure": "M2", "account": "other"}):
        status, _, _ = _get(srv, "/api/lines?" + urlencode(q))
        assert status == 400, q
    status, _, _ = _get(srv, "/api/lines?figure=M2&figure=M3")
    assert status == 400


@pytest.mark.contracts
def test_a_case_of_two_accounts_names_the_account(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    _case(tmp_path, RICH.replace("L", "S").replace("2026-", "2025-"), account="savings", case_id=case_id)
    srv = serve(case_id)
    assert _get(srv, "/api/lines?figure=M2")[0] == 400
    d = _lines(srv, figure="M2", account="savings")
    assert d["account"] == "savings" and [x["id"] for x in d["lines"]] == ["S1", "S2", "S3", "S15"]


@pytest.mark.contracts
def test_a_case_that_does_not_reconcile_shows_no_lines(home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)
    real = overrides.categorise

    def broken(statement, *args, **kwargs):
        out = real(statement, *args, **kwargs)
        for t in statement.transactions:
            if t.id == "L2":
                t.category = overrides.Category.SUPPLIER
        return out

    monkeypatch.setattr(overrides, "categorise", broken)
    srv = serve(case_id)
    d = _lines(srv, figure="M2")
    assert d["state"] == "not_reconciled" and "lines" not in d
    status, _, page = _get(srv, "/lines?figure=M2")
    assert status == 200 and b"No figures are shown" in page


# --- the page ------------------------------------------------------------------------------


@pytest.mark.contracts
def test_every_figure_bucket_and_cell_on_the_page_links_to_its_lines(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    srv = serve(case_id)
    _, _, page = _get(srv, "/")
    page = page.decode()
    f = overrides.case_figures(folder)["main"]
    for m in f.metrics:
        assert escape("/lines?" + urlencode({"token": srv.token, "account": "main", "figure": m.id})) in page
    for row in f.monthly:
        for key, _ in summary.CELLS:
            target = {"token": srv.token, "account": "main", "month": row.month, "cell": key}
            assert escape("/lines?" + urlencode(target)) in page


@pytest.mark.contracts
def test_the_lines_page_shows_the_lines_and_the_total(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    status, kind, page = _get(srv, "/lines?figure=M8")
    page = page.decode()
    assert status == 200 and kind.startswith("text/html")
    for text in ("DD IWOCA LTD", "UNPAID DD IWOCA LTD", "-£400.00", "the figure negated", "v2:returned_dd",
                 "loan_repayment"):
        assert text in page
    assert "<script>" not in page.split("</head>", 1)[1]


@pytest.mark.invariants
def test_p8_the_lines_page_holds_description_text_only_escaped(home, tmp_path, serve):
    desc = '<script>alert("x")</script> & </td><a href="javascript:1">'
    body = RICH.replace("XQZ 4471", '"' + desc.replace('"', '""') + '"')
    case_id, _ = _case(tmp_path, body)
    srv = serve(case_id)
    status, _, page = _get(srv, "/lines?figure=M25")
    page = page.decode()
    assert status == 200 and desc not in page and escape(desc, quote=True) in page
    assert _lines(srv, figure="M25")["lines"][1]["description"] == desc


# --- hardening: the new entry points' checks, and a share's inputs ---------------------------


def _raw(srv, path, headers):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


@pytest.mark.contracts
def test_the_lines_entry_points_refuse_a_request_without_the_token_or_the_loopback_host(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    host = f"127.0.0.1:{srv.port}"
    for path in ("/lines?figure=M2", "/api/lines?figure=M2"):
        assert _raw(srv, path, {"Host": host})[0] == 403  # no token
        assert _raw(srv, path, {"Host": host, "X-CMA-Token": "wrong"})[0] == 403
        assert _raw(srv, path, {"Host": "evil.example", "X-CMA-Token": srv.token})[0] == 403
        assert _raw(srv, path + f"&token={srv.token}", {"Host": host, "Origin": "http://evil.example"})[0] == 403
        status, _ = _raw(srv, path + f"&token={srv.token}", {"Host": host})
        assert status == 200


@pytest.mark.contracts
def test_an_unforeseen_failure_in_the_lines_is_answered_500_naming_nothing(home, tmp_path, serve, monkeypatch):
    from cma.review import drilldown

    case_id, _ = _case(tmp_path)
    srv = serve(case_id)

    def boom(*a, **k):
        raise RuntimeError("CARD SETTLEMENT STRIPE secret")

    monkeypatch.setattr(drilldown, "lines_behind", boom)
    for path in ("/lines?figure=M2", "/api/lines?figure=M2"):
        status, _, body = _get(srv, path)
        assert status == 500 and b"STRIPE" not in body


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(10))
def test_p2_a_share_s_inputs_give_the_figure(home, tmp_path, serve, seed):
    rng = random.Random(seed)
    words = ["FPS CREDIT ACME LTD INV 1", "FPS CREDIT BETA LTD INV 2", "FPS CREDIT GAMMA LTD INV 3",
             "STRIPE PAYOUT", "XQZ 4471", "IWOCA LOAN", "PAYPAL REFUND", "INTEREST PAID"]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["id", "date", "description", "amount", "balance"])
    balance = 0
    for n in range(rng.randint(2, 20)):
        p = rng.randint(1, 500_000)
        balance += p
        w.writerow([f"L{n + 1}", f"2026-01-{n + 1:02d}", rng.choice(words), f"{p / 100:.2f}", f"{balance / 100:.2f}"])
    case_id, folder = _case(tmp_path, out.getvalue())
    srv = serve(case_id)
    by = {m.id: m for m in overrides.case_figures(folder)["main"].metrics}
    for fid in ("M15", "M16", "M26"):
        m = by[fid]
        d = _lines(srv, figure=fid)
        money = [i for i in d["inputs"] if i.get("figure") is None]
        if m.value is None or not m.evidence:
            continue
        num, den = _pence(money[0]["display"]), _pence(money[-1]["display"])
        assert den == metrics.lines_total(overrides.case_figures(folder)["main"].statement, m.evidence)
        assert metrics._ratio(num, den, 2, 100) == m.value


@pytest.mark.contracts
def test_the_drilldown_reads_the_profile_once_with_the_figures(home, tmp_path, serve, monkeypatch):
    """M28's proposed repayment is the one the figure was computed with: the drill-down takes
    it from the figures, never from a second read of the profile (verify-241)."""
    case_id, _ = _case(tmp_path)
    srv = serve(case_id)
    reads = []
    real = store.load_profile
    monkeypatch.setattr(store, "load_profile", lambda folder: reads.append(1) or real(folder))
    d = _lines(srv, figure="M28")
    assert len(reads) == 1
    assert {"label": "Proposed monthly repayment (case profile)", "figure": None, "display": "£250.00"} in d["inputs"]


@pytest.mark.invariants
@pytest.mark.parametrize("archetype, profile", [("cafe", "good"), ("trades", "stretched"), ("ecommerce", "poor")])
def test_every_derived_figure_on_generated_statements_shows_inputs_that_give_it(home, tmp_path, archetype,
                                                                               profile):
    """R16 (verify-241): a derived figure's drill-down must show what it is computed from. The
    earlier test only checked that inputs was a list, so M4, M7, M9 and M13 passed with none:
    every derived figure now shows inputs or the closes it reads, and M4's two quarters give it."""
    from datetime import date

    from cma import synth
    from cma.review import drilldown

    rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
    synth.write(rows, truth, tmp_path, stem=archetype)
    case_id = store.new_case(label="t20")
    folder = store.case_folder(case_id)
    store.update_profile(folder, lambda p: {**p, "vat_stagger": 1, "proposed_monthly_repayment": "1500.00"})
    store.import_file(folder, tmp_path / f"{archetype}.csv", "csv")
    f = overrides.case_figures(folder)["main"]
    for m in f.metrics:
        if metrics.figure_kind(m.id) != "derived":
            continue
        d = drilldown.lines_behind(folder, case_id, figure=m.id)
        assert d["inputs"] or d["closes"], m.id  # never a derived figure with nothing behind it
        assert d["method"], m.id
    m4 = next(m for m in f.metrics if m.id == "M4")
    if m4.value is not None:
        quarters = [i for i in drilldown.lines_behind(folder, case_id, figure="M4")["inputs"] if "quarter" in i["label"]]
        assert len(quarters) == 2
        early, late = (_pence(q["display"]) for q in quarters)
        assert round((late - early) / early * 100, 1) == round(m4.value, 1)
