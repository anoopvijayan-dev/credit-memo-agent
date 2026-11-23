"""T34: own-account pairing across a case's accounts within the configured days (R8, R9, P11).

A debit in one account pairs with the single credit of the same amount in another account of
the case dated within the configured number of days of it (firm.yaml's
own_account_pairing_days, 3 by default), and both become own-account transfers. A debit with
more than one candidate credit is not paired, and it and every candidate are listed in the
workspace judgement queue as a possible own-account transfer (R9).

The P11 cases are generated from a fixed seed: each holds credits from outside the
applicant's accounts, debits to outside, and moves between its accounts, every amount
distinct but a move's two halves, so the generator knows which credits came from outside."""

import random
from datetime import date, timedelta

import pytest

from cma import firm, metrics, overrides, pack, store
from cma.review import summary

PAIRING = "pairing:"


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _firm(home, text):
    home.mkdir(parents=True, exist_ok=True)
    (home / firm.FILE_NAME).write_text(text, encoding="utf-8")


def _csv(lines, opening=1000_00):
    """lines: (id, date, description, pence), in date order; balances run from `opening`."""
    out, bal = ["id,date,description,amount,balance"], opening
    for i, d, desc, p in lines:
        bal += p
        out.append(f"{i},{d.isoformat()},{desc},{p / 100:.2f},{bal / 100:.2f}")
    return "\n".join(out) + "\n"


def _case(tmp_path, accounts):
    case_id = store.new_case(label="t34")
    folder = store.case_folder(case_id)
    for account, body in accounts.items():
        src = tmp_path / f"{account}-{case_id}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    return case_id, folder


D0 = date(2026, 3, 2)


def _two(gap):
    """A move of £100.00 out of main and into savings `gap` days later."""
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=1), "FPS TO 12345678", -100_00),
                 ("M3", D0 + timedelta(days=20), "CARD SETTLEMENT STRIPE", 70_00)])
    sav = _csv([("S1", D0, "CARD SETTLEMENT STRIPE", 30_00),
                ("S2", D0 + timedelta(days=1 + gap), "FPS FROM 87654321", 100_00),
                ("S3", D0 + timedelta(days=25), "CARD SETTLEMENT STRIPE", 10_00)])
    return {"main": main, "savings": sav}


def _rules(folder):
    both = overrides.combined_figures(overrides.case_figures(folder))
    return {t.id: t.rule_id for t in both.statement.transactions}


# --- the configured days (R8) -----------------------------------------------------------------


@pytest.mark.contracts
def test_pairing_days_default_to_three(home, tmp_path):
    assert firm.load().own_account_pairing_days == 3
    _, folder = _case(tmp_path, _two(3))
    assert _rules(folder)["main:M2"] == "pairing:savings:S2"
    _, folder = _case(tmp_path, _two(4))
    assert not (_rules(folder)["main:M2"] or "").startswith(PAIRING)


@pytest.mark.contracts
def test_pairing_follows_the_days_configured_in_firm_yaml(home, tmp_path):
    _, folder = _case(tmp_path, _two(6))
    assert not (_rules(folder)["main:M2"] or "").startswith(PAIRING)
    _firm(home, "own_account_pairing_days: 7\n")
    assert _rules(folder)["main:M2"] == "pairing:savings:S2"
    assert _rules(folder)["savings:S2"] == "pairing:main:M2"
    _firm(home, "own_account_pairing_days: 0\n")  # the same day only
    assert not (_rules(folder)["main:M2"] or "").startswith(PAIRING)
    _, same_day = _case(tmp_path, _two(0))
    assert _rules(same_day)["main:M2"] == "pairing:savings:S2"


@pytest.mark.contracts
@pytest.mark.parametrize("value", ["-1", "1.5", "true", "'3'", "32", "[3]"])
def test_a_malformed_pairing_days_setting_is_refused(home, value):
    _firm(home, f"own_account_pairing_days: {value}\n")
    with pytest.raises(firm.FirmConfigError, match="own_account_pairing_days"):
        firm.load()


@pytest.mark.contracts
def test_the_configured_days_change_the_workspace_queue(home, tmp_path):
    # Two credits of the debit's amount, one 2 days on and one 6 days on: with 3 days the
    # debit pairs with the first; with 7 both are candidates, so it is unpaired and listed (R9).
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=1), "FPS TO 12345678", -100_00)])
    sav = _csv([("S1", D0 + timedelta(days=3), "FPS FROM 87654321", 100_00)])
    res = _csv([("R1", D0 + timedelta(days=7), "FPS FROM 11112222", 100_00)])
    case_id, folder = _case(tmp_path, {"main": main, "savings": sav, "reserve": res})
    assert _rules(folder)["main:M2"] == "pairing:savings:S1"
    _firm(home, "own_account_pairing_days: 7\n")
    assert not (_rules(folder)["main:M2"] or "").startswith(PAIRING)
    queue = summary.case_summary(folder, case_id)["queue"]
    listed = {(e["account"], e["line"]["id"]): set(e["pairs_with"]) for e in queue
              if "possible_own_account_transfer" in e["reasons"]}
    assert listed == {("main", "M2"): {"savings:S1", "reserve:R1"},
                      ("savings", "S1"): {"main:M2", "reserve:R1"},
                      ("reserve", "R1"): {"main:M2", "savings:S1"}}


@pytest.mark.contracts
def test_the_preview_snapshot_binds_the_pairing_days(home, tmp_path):
    case_id, folder = _case(tmp_path, _two(2))
    before = pack.build_preview(folder, case_id).inputs
    assert before["own_account_pairing_days"] == 3
    _firm(home, "own_account_pairing_days: 5\n")
    after = pack.build_preview(folder, case_id).inputs
    assert after["own_account_pairing_days"] == 5
    assert pack.changed_inputs(before, after) == ["the own-account pairing days"]


# --- P11 on generated multi-account cases ------------------------------------------------------


def _generate(rng, accounts, *, clash):
    """One case: per-account lines, and the pence of the credits from outside its accounts.
    Every amount is distinct but a move's two halves; with `clash`, one move's debit also meets
    a second credit of its amount in a third account within the days (two candidates, R9)."""
    amounts = iter(rng.sample(range(1_00, 900_00), 60))
    lines = {a: [] for a in accounts}
    outside_in = 0
    for _ in range(rng.randint(2, 6)):
        a, p, d = rng.choice(accounts), next(amounts), D0 + timedelta(days=rng.randint(0, 40))
        lines[a].append((d, "CARD SETTLEMENT STRIPE", p))
        outside_in += p
    for _ in range(rng.randint(1, 4)):
        lines[rng.choice(accounts)].append((D0 + timedelta(days=rng.randint(0, 40)), "DD COUNCIL", -next(amounts)))
    moves = []
    for _ in range(rng.randint(1, 4)):
        src, dst = rng.sample(accounts, 2)
        p, d = next(amounts), D0 + timedelta(days=rng.randint(0, 40))
        lines[src].append((d, "FPS TO 12345678", -p))
        lines[dst].append((d + timedelta(days=rng.randint(0, 3)), "FPS FROM 87654321", p))
        moves.append((src, dst, p, d))
    if clash:
        src, dst, p, d = moves[0]
        third = next(a for a in accounts if a not in (src, dst))
        lines[third].append((d + timedelta(days=rng.randint(0, 3)), "FPS FROM 55556666", p))
    bodies = {}
    for a, ls in lines.items():
        ls.sort(key=lambda x: x[0])
        bodies[a] = _csv([(f"{a[0].upper()}{n}", d, desc, p) for n, (d, desc, p) in enumerate(ls, start=1)],
                         opening=rng.randint(0, 5_000_00))
    return bodies, outside_in


def _p11_structure(folder, days):
    """Each own-account pair is one debit and one credit of equal amount in different accounts,
    within the days, each naming the other; no line is in two pairs. Returns the combined figures."""
    figures = overrides.case_figures(folder)
    both = overrides.combined_figures(figures)
    lines = {t.id: t for t in both.statement.transactions}
    seen: dict[str, str] = {}
    for cid, t in lines.items():
        if not (t.rule_id or "").startswith(PAIRING):
            continue
        assert t.category == metrics.Category.OWN_ACCOUNT_TRANSFER
        other_id = t.rule_id[len(PAIRING):]
        assert other_id in lines, other_id
        other = lines[other_id]
        assert other.rule_id == PAIRING + cid                      # it names this line back
        assert other.account_key != t.account_key                   # different accounts
        assert metrics.size_pence(other) == metrics.size_pence(t)   # equal amount
        assert (t.amount > 0) != (other.amount > 0)                 # one debit, one credit
        assert abs((t.date - other.date).days) <= days
        assert cid not in seen                                      # no line in two pairs
        seen[cid] = other_id
    return figures, both, lines


def _pence(m):
    return round(m.value * 100)


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(12))
def test_p11_generated_cases_pair_moves_and_leave_outside_money(home, tmp_path, seed):
    rng = random.Random(seed)
    accounts = ["main", "savings", "reserve"][: rng.choice((2, 3))]
    bodies, outside_in = _generate(rng, accounts, clash=False)
    _, folder = _case(tmp_path, bodies)
    _, both, lines = _p11_structure(folder, 3)
    m = {x.id: x for x in both.metrics}
    # Total money in less own-account transfers in is the money from outside the accounts.
    assert _pence(m["M17"]) - _pence(m["M19"]) == outside_in
    # Every move pairs: each FPS line is half of a pair.
    assert all((t.rule_id or "").startswith(PAIRING) for t in lines.values() if t.description.startswith("FPS"))


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(8))
def test_p11_a_debit_with_two_candidates_is_unpaired_and_listed_with_every_candidate(home, tmp_path, seed):
    rng = random.Random(1000 + seed)
    accounts = ["main", "savings", "reserve"]
    bodies, _ = _generate(rng, accounts, clash=True)
    case_id, folder = _case(tmp_path, bodies)
    figures, _, lines = _p11_structure(folder, 3)
    groups = next(iter(figures.values())).possible_pairs
    assert groups, "the clash leaves at least one debit unpaired"
    queue = summary.case_summary(folder, case_id)["queue"]
    listed = {f"{e['account']}:{e['line']['id']}": set(e["pairs_with"]) for e in queue
              if "possible_own_account_transfer" in e["reasons"]}
    for group in groups:
        ids = [f"{a}:{i}" for a, i in group]
        assert lines[ids[0]].amount < 0 and len(ids) >= 2  # the debit first, then its candidates
        for i in ids:  # R9: unpaired, and in the queue with every other line of its group
            assert not (lines[i].rule_id or "").startswith(PAIRING)
            assert set(ids) - {i} <= listed[i]


@pytest.mark.contracts
def test_removing_the_pairing_days_setting_restores_the_default(home, tmp_path):
    # Rollback of the setting: delete the key and the shipped 3 days apply again, with no repair.
    _, folder = _case(tmp_path, _two(5))
    _firm(home, "own_account_pairing_days: 7\nshow_policy_verdict: true\n")
    assert _rules(folder)["main:M2"] == "pairing:savings:S2"
    _firm(home, "show_policy_verdict: true\n")
    assert firm.load().own_account_pairing_days == firm.DEFAULT_PAIRING_DAYS == 3
    assert not (_rules(folder)["main:M2"] or "").startswith(PAIRING)


@pytest.mark.contracts
def test_a_malformed_pairing_days_setting_refuses_the_workspace_figures(home, tmp_path):
    # A multi-account case reads the setting; a bad value refuses the figures, never a silent default.
    _, folder = _case(tmp_path, _two(2))
    _firm(home, "own_account_pairing_days: -1\n")
    figures, refused = summary.load_figures(folder)
    assert figures is None and refused["state"] == "refused"
    assert "own_account_pairing_days" in refused["refusal"]


@pytest.mark.contracts
def test_two_debits_with_the_same_single_candidate_are_both_unpaired_and_listed(home, tmp_path):
    # Each debit has one candidate, but it is the same credit: pairing either would leave the
    # other out by statement order, so neither pairs and all three lines are listed (R9, P11).
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=1), "FPS TO 12345678", -100_00)])
    res = _csv([("R1", D0, "CARD SETTLEMENT STRIPE", 321_45), ("R2", D0 + timedelta(days=1), "FPS TO 99998888", -100_00)])
    sav = _csv([("S1", D0 + timedelta(days=2), "FPS FROM 87654321", 100_00)])
    case_id, folder = _case(tmp_path, {"main": main, "reserve": res, "savings": sav})
    rules = _rules(folder)
    assert not any((rules[k] or "").startswith(PAIRING) for k in ("main:M2", "reserve:R2", "savings:S1"))
    queue = summary.case_summary(folder, case_id)["queue"]
    listed = {(e["account"], e["line"]["id"]): set(e["pairs_with"]) for e in queue
              if "possible_own_account_transfer" in e["reasons"]}
    assert listed == {("main", "M2"): {"savings:S1"}, ("reserve", "R2"): {"savings:S1"},
                      ("savings", "S1"): {"main:M2", "reserve:R2"}}


@pytest.mark.contracts
def test_zero_pairing_days_pairs_only_same_day_moves_in_either_order(home, tmp_path):
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=4), "FPS TO 12345678", -100_00)])
    sav = _csv([("S1", D0 + timedelta(days=3), "FPS FROM 87654321", 100_00)])
    _, folder = _case(tmp_path, {"main": main, "savings": sav})
    assert _rules(folder)["main:M2"] == "pairing:savings:S1"  # the credit a day before the debit
    _firm(home, "own_account_pairing_days: 1\n")
    assert _rules(folder)["main:M2"] == "pairing:savings:S1"
    _firm(home, "own_account_pairing_days: 0\n")
    assert not (_rules(folder)["main:M2"] or "").startswith(PAIRING)
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=1), "FPS TO 12345678", -100_00)])
    sav = _csv([("S1", D0 + timedelta(days=1), "FPS FROM 87654321", 100_00)])
    _, same_day = _case(tmp_path, {"main": main, "savings": sav})
    assert _rules(same_day)["main:M2"] == "pairing:savings:S1"


# --- overriding a paired line (Antigravity re-review of T16) -----------------------------------


@pytest.mark.contracts
def test_a_paired_line_can_be_overridden_back_to_what_the_table_gave_it(home, tmp_path):
    # A Stripe payout that the pairing took for the other half of a transfer: moving it back to
    # card receipts was read as no change, since the line's category was looked up unpaired.
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=1), "FPS TO 12345678", -100_00)])
    sav = _csv([("S1", D0, "CARD SETTLEMENT STRIPE", 30_00), ("S2", D0 + timedelta(days=2), "CARD SETTLEMENT STRIPE", 100_00)])
    _, folder = _case(tmp_path, {"main": main, "savings": sav})
    assert _rules(folder)["savings:S2"] == "pairing:main:M2"
    added = overrides.add_override(folder, "S2", "revenue_card_settlement", reason="a real payout", name="Pat",
                                   account="savings")
    assert added.recorded and added.old == "own_account_transfer" and added.new == "revenue_card_settlement"
    rules = _rules(folder)
    assert rules["savings:S2"] == "override:1" and not (rules["main:M2"] or "").startswith(PAIRING)


@pytest.mark.contracts
def test_overriding_a_paired_line_to_an_own_account_transfer_changes_nothing(home, tmp_path):
    main = _csv([("M1", D0, "CARD SETTLEMENT STRIPE", 500_00), ("M2", D0 + timedelta(days=1), "FPS TO 12345678", -100_00)])
    sav = _csv([("S1", D0, "CARD SETTLEMENT STRIPE", 30_00), ("S2", D0 + timedelta(days=2), "FPS FROM 87654321", 100_00)])
    _, folder = _case(tmp_path, {"main": main, "savings": sav})
    added = overrides.add_override(folder, "S2", "own_account_transfer", reason="r", name="Pat", account="savings")
    assert not added.recorded
    assert _rules(folder)["main:M2"] == "pairing:savings:S2"
