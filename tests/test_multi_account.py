"""T33: a case of more than one account (R41).

When a case holds more than one account, its figures across all its accounts have each line's
id prefixed with its account key, so ids are unique within the case; flow figures count every
account's lines; and balance figures read the combined end-of-day balance over the days every
account covers, n/a with the reason when the accounts' periods do not overlap. The workspace
shows them as "All accounts". An import that would bring the case's combined money in or money
out to £10,000,000,000,000 or more is refused, naming the account, and leaves the case as it was.

The two-account fixtures are worked by hand in the comments beside them."""

import http.client
import json
from urllib.parse import urlencode

import pytest

from cma import case as caselog
from cma import metrics, overrides, store
from cma.review import drilldown, summary
from cma.review import server as rs

# main closes: 01-05..09 1,500.00 (M1), 01-10..19 300.00 (M2), 01-20 400.00 (M3).
MAIN = ("id,date,description,amount,balance\n"
        "M1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
        "M2,2026-01-10,DD COUNCIL,-1200.00,300.00\n"
        "M3,2026-01-20,CARD SETTLEMENT STRIPE,100.00,400.00\n")
# savings closes: 01-08..14 -500.00 (S1), 01-15..24 -450.00 (S2), 01-25 -440.00 (S3).
SAVINGS = ("id,date,description,amount,balance\n"
           "S1,2026-01-08,DD RENT,-700.00,-500.00\n"
           "S2,2026-01-15,CARD SETTLEMENT STRIPE,50.00,-450.00\n"
           "S3,2026-01-25,CARD SETTLEMENT STRIPE,10.00,-440.00\n")
# The same lines a month later: no day is covered by both accounts.
LATER = SAVINGS.replace("2026-01-", "2026-02-")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _case(tmp_path, *accounts):
    case_id = store.new_case(label="t33")
    folder = store.case_folder(case_id)
    for account, body in accounts:
        src = tmp_path / f"{account}-{case_id}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    return case_id, folder


def _by_id(f):
    return {m.id: m for m in f.metrics}


@pytest.mark.contracts
def test_two_accounts_overlapping_combined_figures(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    figures = overrides.case_figures(folder)
    assert set(figures) == {"main", "savings"}  # each account's own figures are kept, ids unprefixed
    assert [t.id for t in figures["main"].statement.transactions] == ["M1", "M2", "M3"]
    both = overrides.combined_figures(figures)
    assert both.account == overrides.COMBINED
    ids = [t.id for t in both.statement.transactions]
    assert ids == ["main:M1", "savings:S1", "main:M2", "savings:S2", "main:M3", "savings:S3"]
    assert len(set(ids)) == len(ids)
    assert [t.account_key for t in both.statement.transactions] == ["main", "savings"] * 3
    m = _by_id(both)
    # Flow figures across both accounts.
    assert m["M17"].value == 660.00 and m["M2"].value == 660.00
    assert m["M17"].evidence == ["main:M1", "savings:S2", "main:M3", "savings:S3"]
    assert metrics.lines_total(both.statement, m["M2"].evidence) == 66000
    # Combined closes over 01-08..01-20, the days both cover:
    # 01-08..09 1,000.00; 01-10..14 -200.00; 01-15..19 -150.00; 01-20 -50.00.
    assert m["M5"].value == -200.00
    assert m["M5"].evidence == ["savings:S1", "main:M2"]  # each account's line that set 01-10's close
    assert m["M6"].value == 11
    assert m["M6"].evidence == ["savings:S1", "main:M2", "savings:S2", "main:M3"]
    # (2 * 1,000.00 + 5 * -200.00 + 5 * -150.00 - 50.00) / 13 = 15.3846..., half to even 15.38
    assert m["M27"].value == 15.38
    assert m["M27"].evidence == ["main:M1", "savings:S1", "main:M2", "savings:S2", "main:M3"]
    assert "every account covers" in m["M5"].method
    [jan] = both.monthly
    assert jan.money_in.value == 660.00 and jan.money_out.value == 1900.00
    assert jan.lowest_balance.value == -200.00 and jan.days_overdrawn.value == 11
    assert jan.average_balance.value == 15.38
    assert jan.lowest_balance.evidence == m["M27"].evidence


@pytest.mark.contracts
def test_two_accounts_not_overlapping_balance_figures_na(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", LATER))
    both = overrides.combined_figures(overrides.case_figures(folder))
    m = _by_id(both)
    assert m["M17"].value == 660.00  # flow figures are still combined
    for fid in ("M5", "M6", "M27"):
        assert m[fid].value is None, fid
        assert m[fid].method == metrics.NO_SHARED_DAYS and m[fid].evidence == []
    assert [r.month for r in both.monthly] == ["2026-01", "2026-02"]
    for row in both.monthly:
        assert row.money_in.value is not None
        for cell in ("average_balance", "lowest_balance", "days_overdrawn"):
            c = getattr(row, cell)
            assert c.value is None and c.note == metrics.NO_SHARED_DAYS and c.evidence == []


@pytest.mark.contracts
def test_month_with_no_shared_day_is_na_others_read(home, tmp_path):
    # savings covers 01-08 to 02-03; main only January: February has no day both cover.
    later = SAVINGS.replace("S3,2026-01-25", "S3,2026-02-03")
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", later))
    jan, feb = overrides.combined_figures(overrides.case_figures(folder)).monthly
    assert jan.lowest_balance.value == -200.00
    assert feb.lowest_balance.value is None and feb.lowest_balance.note == metrics.NO_SHARED_DAYS_IN_MONTH
    assert feb.money_in.value == 10.00


@pytest.mark.contracts
def test_one_account_has_no_combined_figures(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN))
    figures = overrides.case_figures(folder)
    assert overrides.combined_figures(figures) is None
    assert [t.id for t in figures["main"].statement.transactions] == ["M1", "M2", "M3"]


@pytest.mark.contracts
def test_own_account_transfer_across_accounts_stays_out_of_trading(home, tmp_path):
    main = MAIN + "M4,2026-01-21,FPS CREDIT ACME HOLDINGS,25.00,425.00\n"
    _, folder = _case(tmp_path, ("main", main), ("savings", SAVINGS))
    store.update_profile(folder, lambda p: {**p, "own_accounts": ["ACME HOLDINGS"]})
    m = _by_id(overrides.combined_figures(overrides.case_figures(folder)))
    assert m["M19"].evidence == ["main:M4"] and m["M19"].value == 25.00
    assert m["M2"].value == 660.00 and "main:M4" not in m["M9"].evidence


@pytest.mark.contracts
def test_override_marks_combined_figures(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    overrides.add_override(folder, "S2", "other", reason="not trading", name="Pat", account="savings")
    both = overrides.combined_figures(overrides.case_figures(folder))
    m = _by_id(both)
    assert m["M2"].value == 610.00 and m["M25"].evidence == ["savings:S2"]
    assert "savings:S2" in both.overridden and "M25" in both.marked


def _api_and_page(folder, case_id):
    srv = rs.start(folder, case_id, port=0)
    srv.serve_in_thread()
    try:
        out = []
        for path in ("/api/case", "/", "/lines?" + urlencode({"account": "*", "figure": "M5"})):
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{srv.port}", "X-CMA-Token": srv.token})
            r = conn.getresponse()
            assert r.status == 200, path
            out.append(r.read().decode("utf-8"))
            conn.close()
        return json.loads(out[0]), out[1], out[2]
    finally:
        srv.close()


@pytest.mark.contracts
def test_workspace_shows_all_accounts(home, tmp_path):
    case_id, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    api, page, lines = _api_and_page(folder, case_id)
    c = api["combined"]
    assert c["account"] == "*" and c["line_count"] == 6
    assert c["period"] == {"start": "2026-01-05", "end": "2026-01-25"}
    shown = {f["id"]: f for f in c["figures"]}
    assert shown["M5"]["display"] == "-£200.00" and shown["M17"]["display"] == "£660.00"
    assert [a["account"] for a in api["accounts"]] == ["main", "savings"]
    assert '<section class="account combined" id="combined"><h2>All accounts</h2>' in page
    assert summary.COMBINED_BASIS.split(":")[0] in page
    # The drill-down of a combined figure lists the prefixed lines, and each line's override form
    # names its own account and its own id.
    assert "All accounts: <strong>-£200.00</strong>" in lines
    assert "<td>savings:S1</td>" in lines and "<td>main:M2</td>" in lines
    assert 'name="account" value="savings"' in lines and 'name="line" value="S1"' in lines
    assert "Combined close on 2026-01-10" in lines


@pytest.mark.contracts
def test_workspace_not_overlapping_says_why(home, tmp_path):
    case_id, folder = _case(tmp_path, ("main", MAIN), ("savings", LATER))
    s = summary.case_summary(folder, case_id)
    shown = {f["id"]: f for f in s["combined"]["figures"]}
    assert shown["M5"]["display"] == "n/a" and shown["M5"]["method"] == metrics.NO_SHARED_DAYS
    html = summary.render(s)
    assert summary._e(metrics.NO_SHARED_DAYS) in html
    d = drilldown.lines_behind(folder, case_id, account="*", month="2026-02", cell="lowest_balance")
    assert d["value"] is None and d["note"] == metrics.NO_SHARED_DAYS and d["lines"] == []


@pytest.mark.contracts
def test_one_account_workspace_has_no_combined(home, tmp_path):
    case_id, folder = _case(tmp_path, ("main", MAIN))
    s = summary.case_summary(folder, case_id)
    assert s["combined"] is None and 'id="combined"' not in summary.render(s)


@pytest.mark.contracts
def test_import_refused_at_combined_limit(home, tmp_path):
    big = 6_000_000_000_000
    a = f"id,date,description,amount,balance\nA1,2026-01-05,CARD SETTLEMENT STRIPE,{big}.00,{big}.00\n"
    b = (f"id,date,description,amount,balance\nB1,2026-01-07,CARD SETTLEMENT STRIPE,{big}.00,{big}.00\n"
         f"B2,2026-01-08,DD RENT,-1.00,{big - 1}.00\n")
    _, folder = _case(tmp_path, ("main", a))
    before = len(caselog.for_case(folder).entries())
    src = tmp_path / "b.csv"
    src.write_text(b, encoding="utf-8")
    with pytest.raises(store.CaseStoreError) as e:
        store.import_file(folder, src, "csv", account="second")
    msg = str(e.value)
    assert "account second" in msg and "money in" in msg and "money out" not in msg and "R41" in msg
    assert len(caselog.for_case(folder).entries()) == before
    assert not (folder / store.STATEMENTS / "second.csv").exists()
    assert set(overrides.case_figures(folder)) == {"main"}
    # Below the limit together, the same account is added.
    small = tmp_path / "c.csv"
    small.write_text(SAVINGS, encoding="utf-8")
    store.import_file(folder, small, "csv", account="savings")


@pytest.mark.contracts
def test_import_refused_naming_both_flows(home, tmp_path):
    big = 6_000_000_000_000
    a = (f"id,date,description,amount,balance\nA1,2026-01-05,CARD SETTLEMENT STRIPE,{big}.00,{big}.00\n"
         f"A2,2026-01-06,DD RENT,-{big}.00,0.00\n")
    _, folder = _case(tmp_path, ("main", a))
    src = tmp_path / "b.csv"
    src.write_text(a.replace("A1", "B1").replace("A2", "B2").replace("2026-01-0", "2026-02-0"), encoding="utf-8")
    with pytest.raises(store.CaseStoreError, match="money in and money out"):
        store.import_file(folder, src, "csv", account="second")
