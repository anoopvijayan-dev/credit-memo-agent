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


@pytest.mark.contracts
def test_override_moving_a_line_out_marks_combined_figures(home, tmp_path):
    # S2 (a £50 card settlement) moved to unexplained: combined M2 falls from 660.00 to 610.00,
    # though S2 is no longer among its lines, so M2 and January's trading-receipts cell rest on
    # the override, as each account's figures say (R17, R41).
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    overrides.add_override(folder, "S2", "other", reason="not trading", name="Pat", account="savings")
    figures = overrides.case_figures(folder)
    both = overrides.combined_figures(figures)
    assert _by_id(both)["M2"].value == 610.00 and "savings:S2" not in _by_id(both)["M2"].evidence
    assert "M2" in figures["savings"].marked
    assert "M2" in both.marked and "M25" in both.marked
    assert ("2026-01", "trading_receipts") in both.marked_cells
    # A figure the override leaves as it was, and whose lines hold no overridden line, is not marked.
    assert "M8" not in both.marked and ("2026-01", "money_out") not in both.marked_cells


@pytest.mark.contracts
def test_combined_figures_say_which_account_is_left_out(home, tmp_path):
    case_id, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS), ("third", LATER))
    (folder / store.STATEMENTS / "third.csv").unlink()
    figures = overrides.case_figures(folder)
    both = overrides.combined_figures(figures)
    assert set(figures) == {"main", "savings"} and both.missing == ["third"]
    s = summary.case_summary(folder, case_id)
    assert s["combined"]["missing"] == ["third"]
    assert "third" in s["combined"]["basis"] and "left out" in s["combined"]["basis"]
    assert "left out" in summary.render(s)


@pytest.mark.contracts
def test_import_refused_when_an_account_cannot_be_read_for_the_limit(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    (folder / store.STATEMENTS / "savings.csv").write_text("not,a,statement\n", encoding="utf-8")
    before = len(caselog.for_case(folder).entries())
    src = tmp_path / "third.csv"
    src.write_text(LATER, encoding="utf-8")
    with pytest.raises(store.CaseStoreError) as e:
        store.import_file(folder, src, "csv", account="third")
    assert "account savings" in str(e.value) and "R41" in str(e.value)
    assert len(caselog.for_case(folder).entries()) == before
    assert not (folder / store.STATEMENTS / "third.csv").exists()


@pytest.mark.contracts
@pytest.mark.parametrize("key", ["*", "a:b"])
def test_combined_key_and_colon_are_never_account_keys(home, tmp_path, key):
    _, folder = _case(tmp_path, ("main", MAIN))
    src = tmp_path / "x.csv"
    src.write_text(SAVINGS, encoding="utf-8")
    with pytest.raises(store.CaseStoreError, match="account key"):
        store.import_file(folder, src, "csv", account=key)


@pytest.mark.contracts
def test_reimport_in_two_account_case_changes_nothing(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    head = caselog.for_case(folder).entries()
    src = tmp_path / "again.csv"
    src.write_text(SAVINGS, encoding="utf-8")
    got = store.import_file(folder, src, "csv", account="savings")
    assert got.already and caselog.for_case(folder).entries() == head


@pytest.mark.contracts
def test_combined_lines_need_the_token(home, tmp_path):
    case_id, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    srv = rs.start(folder, case_id, port=0)
    srv.serve_in_thread()
    try:
        path = "/lines?" + urlencode({"account": "*", "figure": "M5"})
        for headers in ({}, {"X-CMA-Token": srv.token[:-1] + ("x" if srv.token[-1] != "x" else "y")}):
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{srv.port}", **headers})
            r = conn.getresponse()
            body = r.read().decode("utf-8")
            conn.close()
            assert r.status == 403 and "main:M2" not in body
    finally:
        srv.close()


@pytest.mark.contracts
def test_override_on_combined_key_is_refused(home, tmp_path):
    # "*" names the case's combined figures, never an account: an override against it is refused
    # and records nothing (R17, R41).
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    before = caselog.for_case(folder).entries()
    with pytest.raises(store.CaseStoreError, match="no account"):
        overrides.add_override(folder, "main:M1", "other", reason="x", name="Pat", account=overrides.COMBINED)
    assert caselog.for_case(folder).entries() == before


@pytest.mark.contracts
def test_refused_combined_import_leaves_no_input_file(home, tmp_path):
    big = 6_000_000_000_000
    a = f"id,date,description,amount,balance\nA1,2026-01-05,CARD SETTLEMENT STRIPE,{big}.00,{big}.00\n"
    _, folder = _case(tmp_path, ("main", a))
    inputs = sorted(p.name for p in (folder / store.INPUTS).iterdir())
    src = tmp_path / "b.csv"
    src.write_text(a.replace("A1", "B1").replace("2026-01-05", "2026-02-05"), encoding="utf-8")
    with pytest.raises(store.CaseStoreError, match="R41"):
        store.import_file(folder, src, "csv", account="second")
    assert sorted(p.name for p in (folder / store.INPUTS).iterdir()) == inputs


@pytest.mark.contracts
def test_combined_buckets_reconcile_to_money_in(home, tmp_path):
    _, folder = _case(tmp_path, ("main", MAIN), ("savings", SAVINGS))
    m = _by_id(overrides.combined_figures(overrides.case_figures(folder)))
    parts = sum(round(m[i].value * 100) for i in ("M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25"))
    assert parts == round(m["M17"].value * 100)
    assert set(m["M18"].evidence) <= set(m["M2"].evidence)


# --- own-account pairing across the case's accounts (R8, R9, R41) -------------------------

# A move from main to savings that neither the rule table nor the profile names: the debit
# and the credit are only an own-account transfer because they pair, £100.00 out of main and
# £100.00 into savings a day later.
PAIR_MAIN = ("id,date,description,amount,balance\n"
             "M1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
             "M2,2026-01-06,FPS TO 12345678,-100.00,1400.00\n"
             "M3,2026-01-20,CARD SETTLEMENT STRIPE,100.00,1500.00\n")
PAIR_SAVINGS = ("id,date,description,amount,balance\n"
                "S1,2026-01-05,CARD SETTLEMENT STRIPE,50.00,50.00\n"
                "S2,2026-01-07,FPS FROM 87654321,100.00,150.00\n"
                "S3,2026-01-25,CARD SETTLEMENT STRIPE,10.00,160.00\n")
# A third account with a credit of the same amount within the days: two candidates.
PAIR_RESERVE = ("id,date,description,amount,balance\n"
                "R1,2026-01-05,CARD SETTLEMENT STRIPE,20.00,20.00\n"
                "R2,2026-01-06,FPS FROM 12345678,100.00,120.00\n")


@pytest.mark.invariants
def test_p11_unnamed_transfer_between_accounts_is_paired_and_left_out_of_combined_flows(home, tmp_path):
    _, folder = _case(tmp_path, ("main", PAIR_MAIN), ("savings", PAIR_SAVINGS))
    figures = overrides.case_figures(folder)
    both = overrides.combined_figures(figures)
    lines = {t.id: t for t in both.statement.transactions}
    debit, credit = lines["main:M2"], lines["savings:S2"]
    assert debit.category == credit.category == metrics.Category.OWN_ACCOUNT_TRANSFER
    assert debit.rule_id == "pairing:savings:S2" and credit.rule_id == "pairing:main:M2"
    m = _by_id(both)
    # Money in counts the credit in full (R1), but in own-account transfers in, not unexplained.
    assert m["M19"].value == 100.00 and m["M19"].evidence == ["savings:S2"]
    assert m["M25"].value == 0.0 and "savings:S2" not in m["M25"].evidence
    # Trading receipts, and the money in that is not a share's (M16, M26), leave the pair out.
    assert m["M2"].value == 660.00 and "savings:S2" not in m["M2"].evidence
    assert "savings:S2" not in m["M26"].evidence and "savings:S2" not in m["M16"].evidence
    # Money in less own-account transfers in is the money from outside the accounts (P11).
    assert round(m["M17"].value - m["M19"].value, 2) == 660.00
    # Operating cash flow leaves both halves of the pair out, as for a profile's own account (R6).
    assert "main:M2" not in m["M9"].evidence and "savings:S2" not in m["M9"].evidence
    # Each account's own figures carry the pairing too.
    assert _by_id(figures["savings"])["M19"].evidence == ["S2"]
    # A paired line is settled: it is not in the judgement queue.
    queue = summary.case_summary(folder, "t33")["queue"]
    assert not [e for e in queue if e["line"]["id"] in ("M2", "S2")]


@pytest.mark.invariants
def test_p11_two_equal_candidates_leave_the_debit_unpaired_and_listed(home, tmp_path):
    _, folder = _case(tmp_path, ("main", PAIR_MAIN), ("savings", PAIR_SAVINGS), ("reserve", PAIR_RESERVE))
    figures = overrides.case_figures(folder)
    both = overrides.combined_figures(figures)
    lines = {t.id: t for t in both.statement.transactions}
    assert lines["main:M2"].category != metrics.Category.OWN_ACCOUNT_TRANSFER
    assert lines["savings:S2"].category == lines["reserve:R2"].category == metrics.Category.OTHER
    m = _by_id(both)
    assert {"savings:S2", "reserve:R2"} <= set(m["M25"].evidence)
    assert m["M19"].value == 0.0
    # R9: the debit and every candidate are listed as a possible own-account transfer.
    queue = summary.case_summary(folder, "t33")["queue"]
    listed = {(e["account"], e["line"]["id"]): e for e in queue
              if "possible_own_account_transfer" in e["reasons"]}
    assert set(listed) == {("main", "M2"), ("savings", "S2"), ("reserve", "R2")}
    assert set(listed[("main", "M2")]["pairs_with"]) == {"savings:S2", "reserve:R2"}
    assert set(listed[("savings", "S2")]["pairs_with"]) == {"main:M2", "reserve:R2"}


@pytest.mark.contracts
def test_override_on_a_paired_line_wins_and_unpairs_it(home, tmp_path):
    _, folder = _case(tmp_path, ("main", PAIR_MAIN), ("savings", PAIR_SAVINGS))
    overrides.add_override(folder, "S2", "revenue_transfer", reason="a customer", name="A Person", account="savings")
    figures = overrides.case_figures(folder)
    s2 = {t.id: t for t in figures["savings"].statement.transactions}["S2"]
    m2 = {t.id: t for t in figures["main"].statement.transactions}["M2"]
    assert s2.category == metrics.Category.REVENUE_TRANSFER and s2.rule_id.startswith("override:")
    assert m2.category != metrics.Category.OWN_ACCOUNT_TRANSFER  # its only candidate was overridden
    # main has no override of its own, but its figures moved because of savings' one (R17).
    assert "M9" in figures["main"].marked


@pytest.mark.contracts
def test_lines_outside_the_days_or_in_one_account_do_not_pair(home, tmp_path):
    far = PAIR_SAVINGS.replace("2026-01-07,FPS FROM", "2026-01-15,FPS FROM")
    _, folder = _case(tmp_path, ("main", PAIR_MAIN), ("savings", far))
    both = overrides.combined_figures(overrides.case_figures(folder))
    lines = {t.id: t for t in both.statement.transactions}
    assert lines["savings:S2"].category == metrics.Category.OTHER
    assert lines["main:M2"].category != metrics.Category.OWN_ACCOUNT_TRANSFER


@pytest.mark.contracts
def test_pairing_does_not_depend_on_the_order_accounts_were_imported(home, tmp_path):
    # The same three accounts imported in two orders give the same categories and rules (R8, R9).
    def lines(order):
        _, folder = _case(tmp_path, *order)
        both = overrides.combined_figures(overrides.case_figures(folder))
        return {t.id: (t.category, t.rule_id) for t in both.statement.transactions}

    a = ("main", PAIR_MAIN)
    b = ("savings", PAIR_SAVINGS)
    c = ("reserve", PAIR_RESERVE.replace("R2,2026-01-06,FPS FROM 12345678,100.00", "R2,2026-01-06,FPS FROM 1,99.00")
         .replace("120.00", "119.00"))
    first, second = lines([a, b, c]), lines([c, b, a])
    assert first == second
    assert first["main:M2"][1] == "pairing:savings:S2"


@pytest.mark.contracts
def test_a_protected_credit_is_never_paired(home, tmp_path):
    # A lending drawdown of the same amount as a move out of another account, a day later, keeps
    # its protected verdict and is not taken out of lending drawdowns by a pairing (C1's precedence).
    loan = PAIR_SAVINGS.replace("FPS FROM 87654321", "IWOCA LTD LOAN")
    _, folder = _case(tmp_path, ("main", PAIR_MAIN), ("savings", loan))
    both = overrides.combined_figures(overrides.case_figures(folder))
    lines = {t.id: t for t in both.statement.transactions}
    assert lines["savings:S2"].category == metrics.Category.LOAN_DRAWDOWN
    assert not (lines["main:M2"].rule_id or "").startswith("pairing:")
    assert _by_id(both)["M21"].evidence == ["savings:S2"]
