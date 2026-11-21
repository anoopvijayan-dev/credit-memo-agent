"""T43: the workspace's indicators panel and judgement queue (C5; R14, R15, P19).

The case view lists the judgement queue first, before the money-in breakdown and the figures,
largest amount first (by size, either way), and an indicators panel with each indicator's
lines and the thresholds that fired, a threshold still at its shipped value labelled
illustrative (R14). Indicators and the queue come from the figures layer (indicators.py),
raised from the firm's thresholds file when the case root has one, the packaged one otherwise.

Each test talks to a real server running in a thread, through /api/case and the page."""

import csv
import io
import re

import pytest

from cma import store
from cma.review import server as rs
from tests.test_workspace_summary import _api, _assert_only_escaped, _get, _page


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")  # cash paid in is a cash deposit (R55)
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


def _statement(rows: list[tuple[str, str, str]]) -> str:
    """A five-column statement from (date, description, amount) rows, its balance walked in pence."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["id", "date", "description", "amount", "balance"])
    balance = 1_000_000
    for n, (day, desc, amount) in enumerate(rows, start=1):
        balance += round(float(amount) * 100)
        w.writerow([f"L{n}", day, desc, amount, f"{balance // 100}.{balance % 100:02d}"])
    return out.getvalue()


QUEUE = _statement([
    ("2026-01-05", "CARD SETTLEMENT STRIPE", "6000.00"),  # L1 trading receipts: not queued
    ("2026-01-06", "FPS CREDIT J BLOGGS", "300.25"),      # L2 unexplained
    ("2026-01-07", "CASH PAID IN", "2500.00"),            # L3 cash at the single-deposit limit
    ("2026-01-08", "FPS CREDIT A SMITH", "4000.00"),      # L4 unexplained
    ("2026-01-09", "DD COUNCIL", "-100.00"),              # L5 supplier: not queued
    ("2026-01-10", "FPS CREDIT C JONES", "0.99"),         # L6 unexplained
    ("2026-01-11", "FPS CREDIT D LEE", "300.25"),         # L7 unexplained, same size as L2
    ("2026-01-12", "CARD PAYMENT BET365", "-5000.00"),    # L8 gambling: flagged, the largest
    ("2026-01-13", "CASH PAID IN", "2499.99"),            # L9 cash below the limit: not queued
])
# Cash is £4,999.99 of the month's £10,999.99 trading receipts, below the shipped 50%, and no
# credit is five times the median trading receipt, so only the lines named above are flagged.


def _case(tmp_path, body, *, label="t43"):
    case_id = store.new_case(label=label)
    folder = store.case_folder(case_id)
    src = tmp_path / "main.csv"
    src.write_text(body, encoding="utf-8")
    store.import_file(folder, src, "csv", account="main")
    return case_id, folder


def _indicator(data, kind):
    return [i for i in data["accounts"][0]["indicators"] if i["kind"] == kind]


@pytest.mark.contracts
def test_the_queue_is_largest_amount_first_by_size_either_way(home, tmp_path, serve):
    case_id, _ = _case(tmp_path, QUEUE)
    data = _api(serve(case_id))
    queue = data["queue"]
    # L8 (-£5,000, gambling), L4 (£4,000), L3 (£2,500, cash at the limit), then L2 and L7 at
    # £300.25 in statement order, then L6; explained lines that nothing flagged are not queued.
    assert [e["line"]["id"] for e in queue] == ["L8", "L4", "L3", "L2", "L7", "L6"]
    assert [e["line"]["amount"] for e in queue] == ["-£5,000.00", "£4,000.00", "£2,500.00", "£300.25",
                                                     "£300.25", "£0.99"]
    assert {e["account"] for e in queue} == {"main"}
    assert queue[1]["reasons"] == ["unexplained"] and queue[0]["reasons"] == ["indicator"]
    assert [i["kind"] for i in queue[0]["indicators"]] == ["gambling"]


@pytest.mark.contracts
def test_a_cash_deposit_at_the_single_deposit_limit_is_in_the_queue_with_its_indicator(home, tmp_path, serve):
    case_id, _ = _case(tmp_path, QUEUE)
    data = _api(serve(case_id))
    (flag,) = _indicator(data, "single_cash_deposit")
    assert [x["id"] for x in flag["lines"]] == ["L3"]  # £2,499.99 (L9) is below the limit
    assert flag["lines"][0]["category"] == "cash_deposit" and flag["lines"][0]["amount"] == "£2,500.00"
    (th,) = flag["thresholds"]
    assert th["key"] == "single_cash_deposit.amount_at_least" and th["shown"] == "£2,500.00"
    assert th["illustrative"] is True and flag["illustrative"] is True
    entry = next(e for e in data["queue"] if e["line"]["id"] == "L3")
    assert entry["reasons"] == ["indicator"]
    assert entry["indicators"] == [{"id": flag["id"], "kind": "single_cash_deposit"}]
    assert "L9" not in [e["line"]["id"] for e in data["queue"]]


@pytest.mark.invariants
def test_p19_every_cash_deposit_at_or_above_the_limit_is_in_the_workspace_queue_with_its_indicator(
        home, tmp_path, serve):
    body = _statement([("2026-02-01", "CASH PAID IN", a) for a in ("2500.00", "2499.99", "9000.00", "2500.01")]
                      + [("2026-02-02", "CARD SETTLEMENT STRIPE", "50000.00")])  # cash under half the month
    case_id, _ = _case(tmp_path, body)
    data = _api(serve(case_id))
    flags = _indicator(data, "single_cash_deposit")
    assert sorted(x["id"] for f in flags for x in f["lines"]) == ["L1", "L3", "L4"]
    queued = {e["line"]["id"]: e for e in data["queue"]}
    for f in flags:
        (line,) = f["lines"]
        assert {"id": f["id"], "kind": "single_cash_deposit"} in queued[line["id"]]["indicators"]
    assert "L2" not in queued


@pytest.mark.contracts
def test_the_page_lists_the_queue_first_and_the_indicators_panel_with_its_thresholds(home, tmp_path, serve):
    case_id, _ = _case(tmp_path, QUEUE)
    page = _page(serve(case_id))
    q, ind, money = page.index('id="judgement-queue"'), page.index('class="indicators"'), page.index("Money in</h3>")
    assert q < page.index('id="profile"') and q < money < ind
    queue_html = page[q:page.index("</section>", q)]
    assert [m for m in re.findall(r"<td>(L\d+)</td>", queue_html)] == ["L8", "L4", "L3", "L2", "L7", "L6"]
    panel = page[ind:page.index("</section>", ind)]
    assert "single_cash_deposit.amount_at_least" in panel and "£2,500.00" in panel
    assert "illustrative" in panel and "not your firm" in panel
    assert "Cash deposit at or above the single-deposit limit" in panel
    assert "<td>L3</td>" in panel  # the indicator's line


@pytest.mark.contracts
def test_a_firm_threshold_is_not_labelled_illustrative_and_decides_what_fires(home, tmp_path, serve):
    from cma.indicators import default_thresholds_text

    home.mkdir(parents=True, exist_ok=True)
    (home / "indicators.yaml").write_text(
        default_thresholds_text().replace("amount_at_least: 2500.00", "amount_at_least: 2499.99"), encoding="utf-8")
    case_id, _ = _case(tmp_path, QUEUE)
    data = _api(serve(case_id))
    flags = _indicator(data, "single_cash_deposit")
    assert [x["id"] for f in flags for x in f["lines"]] == ["L3", "L9"]
    for f in flags:
        (th,) = f["thresholds"]
        assert th["shown"] == "£2,499.99" and th["illustrative"] is False and f["illustrative"] is False
    assert "L9" in [e["line"]["id"] for e in data["queue"]]
    (gamb,) = _indicator(data, "gambling")
    assert gamb["thresholds"][0]["illustrative"] is True  # still at its shipped value
    assert data["thresholds"]["source"].endswith("indicators.yaml") and not data["thresholds"]["shipped"]
    page = _page(serve(case_id))
    panel = page[page.index('class="indicators"'):]
    assert "£2,499.99" in panel


@pytest.mark.contracts
def test_a_malformed_firm_thresholds_file_shows_no_figures(home, tmp_path, serve):
    home.mkdir(parents=True, exist_ok=True)
    (home / "indicators.yaml").write_text("large_credit: [\n", encoding="utf-8")
    case_id, _ = _case(tmp_path, QUEUE)
    data = _api(serve(case_id))
    assert data["state"] == "refused" and data["accounts"] == [] and data["queue"] == []
    assert "indicators.yaml" in data["refusal"] and "line" in data["refusal"]


@pytest.mark.contracts
def test_an_ambiguous_credit_in_the_queue_shows_its_candidates_and_their_buckets(home, tmp_path, serve):
    case_id, _ = _case(tmp_path, _statement([("2026-03-01", "FPS CREDIT CASH DEPOSIT", "120.00")]))
    data = _api(serve(case_id))
    (entry,) = data["queue"]
    assert entry["reasons"] == ["unexplained", "ambiguous"]
    by_rule = {c["rule_id"]: c["bucket"] for c in entry["candidates"]}
    assert by_rule["cash_paid_in"] == "trading_receipts" and None in by_rule.values()  # the rail gives none
    page = _page(serve(case_id))
    assert "cash_paid_in" in page and "no bucket" in page


@pytest.mark.invariants
def test_p8_queue_and_indicator_lines_show_descriptions_only_escaped(home, tmp_path, serve):
    desc = '<script>x</script> CASH PAID IN & </td>'
    body = _statement([("2026-01-05", desc, "3000.00"), ("2026-01-06", '<b>FPS CREDIT</b> "Q"', "10.00")])
    case_id, _ = _case(tmp_path, body)
    srv = serve(case_id)
    data = _api(srv)
    assert data["queue"][0]["line"]["description"] == desc
    _assert_only_escaped(_page(srv), [desc, '<b>FPS CREDIT</b> "Q"'], shown=True)


@pytest.mark.contracts
def test_a_case_that_cannot_be_shown_has_no_queue(home, serve):
    case_id = store.new_case(label="empty")
    data = _api(serve(case_id))
    assert data["state"] == "empty" and data["queue"] == []
    status, _, _ = _get(serve(case_id), "/")
    assert status == 200


@pytest.mark.contracts
def test_a_case_s_queue_merges_its_accounts_largest_amount_first():
    from datetime import date

    from cma.indicators import case_judgement_queue
    from cma.models import QueueEntry, Statement, Transaction

    def st(*amounts):
        return Statement(account_name="x", transactions=[Transaction(id=f"L{n}", date=date(2026, 1, n), description="X", amount=a,
                                                   balance=0.0) for n, a in enumerate(amounts, start=1)])

    def q(statement):
        order = sorted(statement.transactions, key=lambda t: -abs(t.amount))
        return [QueueEntry(line_id=t.id, amount=t.amount, reasons=["unexplained"]) for t in order]

    a, b = st(10.0, 300.0), st(-300.0, 50.0)
    merged = case_judgement_queue([("a", a, q(a)), ("b", b, q(b))])
    assert [(acct, e.line_id) for acct, e in merged] == [("a", "L2"), ("b", "L1"), ("b", "L2"), ("a", "L1")]
