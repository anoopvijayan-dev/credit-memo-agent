"""Opt-in synthetic features (R47, P17): real acquirers, director funding, cash deposits (one
at or above the single-deposit limit, one paid in after a similar withdrawal), lenders, and a
second account whose transfers the case profile names as own accounts. Every bucket, every
line's category and every indicator equals the generator's truth, and every existing
archetype and profile still produces a byte-identical statement."""

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cma import categorise, indicators, ingest, metrics, synth
from cma.categorise import ProfileNames, load_ruleset
from cma.cli import app
from cma.models import Bucket, Category

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "synth_archetypes.sha256.json"
END = date(2026, 9, 30)
CASES = [(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES]
# M2 is the trading-receipts bucket; the other seven are M19-M25 (R1).
BUCKET_IDS = {Bucket.TRADING_RECEIPTS: "M2", **{b: mid for b, (mid, _, _) in metrics.BUCKET_METRICS.items()}}


def _accounts(tmp_path, archetype, profile, features=synth.FEATURES, seed=7, months=12):
    """Write the case and read each account back through the loader, categorised with v2 and
    the generated case profile. Returns (truth, [(account truth, statement)])."""
    rows, truth = synth.generate(archetype, profile, seed=seed, months=months, end=END, features=features)
    csv_path, _ = synth.write(rows, truth, tmp_path, stem="case")
    profile_names = ProfileNames(tuple(truth.case_profile["directors"]), tuple(truth.case_profile["own_accounts"]))
    v2 = load_ruleset("v2")
    out = [(truth.__dict__, csv_path)] + [(a, tmp_path / f"case_{a['key']}.csv") for a in truth.accounts]
    return truth, [(t, categorise.categorise(ingest.load_csv(p), v2, profile=profile_names)) for t, p in out]


def _assert_matches_truth(t, s):
    assert {x.id: x.category.value for x in s.transactions} == t["category_by_id"]
    m = {x.id: x for x in metrics.compute(s, vat_stagger=synth.VAT_STAGGER)}
    assert m["M17"].value == pytest.approx(t["total_money_in"], abs=0.001)
    for bucket, mid in BUCKET_IDS.items():
        want = t["buckets"][bucket.value]
        assert m[mid].value == pytest.approx(want["amount"], abs=0.001), mid
        assert sorted(m[mid].evidence) == sorted(want["ids"]), mid
    assert m["M18"].value == pytest.approx(t["cash_deposits"]["amount"], abs=0.001)
    assert sorted(m["M18"].evidence) == sorted(t["cash_deposits"]["ids"])
    got = [(i.kind, i.evidence) for i in indicators.raise_indicators(s)]
    assert got == [(i["kind"], i["lines"]) for i in t["indicators"]]


@pytest.mark.invariants
@pytest.mark.parametrize("archetype,profile", CASES)
def test_p17_featured_case_matches_truth(tmp_path, archetype, profile):
    _, accounts = _accounts(tmp_path, archetype, profile)
    assert len(accounts) == 2  # the business account and the reserve account the profile names
    for t, s in accounts:
        _assert_matches_truth(t, s)


@pytest.mark.invariants
@pytest.mark.parametrize("features", [(f,) for f in synth.FEATURES] + [("cash", "lenders"), ("director", "two_accounts")])
@pytest.mark.parametrize("seed,months", [(3, 12), (11, 6), (5, 24)])
def test_p17_any_feature_set_matches_truth(tmp_path, features, seed, months):
    for archetype in synth.ARCHETYPES:
        truth, accounts = _accounts(tmp_path / archetype, archetype, "stretched", features, seed, months)
        assert truth.features == list(features)
        for t, s in accounts:
            _assert_matches_truth(t, s)


@pytest.mark.invariants
@pytest.mark.parametrize("archetype,profile", CASES)
def test_p17_featured_case_has_every_feature_r47(tmp_path, archetype, profile):
    """R47's ingredients are there, each with the truth P17 checks."""
    _, [(main, s), (reserve, rs)] = _accounts(tmp_path, archetype, profile)
    by_id = s.by_id()
    rules = {x.id: x.rule_id for x in s.transactions}
    # Real acquirer names, from v2's acquirer and gateway lists, are trading receipts.
    acquired = {rules[i] for i in main["buckets"][Bucket.TRADING_RECEIPTS.value]["ids"]}
    assert {"v2:card_acquirers", "v2:payment_gateways", "v2:delivery_platforms"} <= acquired
    # Director funding, by the case profile's name and by the table's wording.
    director = main["buckets"][Bucket.DIRECTOR_FUNDING.value]["ids"]
    assert {"profile:director", "v2:director_funding"} <= {rules[i] for i in director}
    # Own-account transfers across two accounts, named in the profile, in both accounts.
    assert main["buckets"][Bucket.OWN_ACCOUNT_IN.value]["ids"]
    assert reserve["buckets"][Bucket.OWN_ACCOUNT_IN.value]["ids"]
    for st in (s, rs):
        moves = [x for x in st.transactions if x.category == Category.OWN_ACCOUNT_TRANSFER]
        assert moves and all(x.rule_id == "profile:own_account" for x in moves)
        assert any(x.amount < 0 for x in moves) and any(x.amount > 0 for x in moves)
    # Lender lines: a named lender's drawdown and its repayments.
    assert main["buckets"][Bucket.LENDING_DRAWDOWNS.value]["ids"]
    assert any(x.category == Category.LOAN_REPAYMENT and x.rule_id == "v2:lender_repayment" for x in s.transactions)
    # Cash: one deposit at or above the single-deposit limit, and one paid in after a similar
    # withdrawal, each flagged.
    flagged = {i["kind"]: i["lines"] for i in main["indicators"]}
    at_limit = flagged["single_cash_deposit"]
    assert at_limit and all(by_id[i].amount >= 2500 for i in at_limit)
    trip = flagged["cash_round_trip"]
    assert by_id[trip[0]].amount < 0 and by_id[trip[-1]].category == Category.CASH_DEPOSIT
    assert "irregular_acquirer" in flagged


@pytest.mark.invariants
def test_p17_byte_identity_of_existing_archetypes_and_profiles(tmp_path):
    """Every archetype and profile, at several seeds and lengths, writes the statement it
    wrote before the features existed, byte for byte (hashes taken at 25a943c, before them).
    Its truth file is the one it wrote then but for one change T11 makes (design, Migration):
    with v2 the default, the director capital line's truth is director_funding, not other."""
    golden = json.loads(GOLDEN.read_text())
    assert len(golden) == 45
    for key, want in golden.items():
        archetype, profile, seed, months = key.split("/")
        rows, truth = synth.generate(archetype, profile, seed=int(seed), months=int(months), end=END)
        csv_path, truth_path = synth.write(rows, truth, tmp_path / key.replace("/", "_"))
        assert hashlib.sha256(csv_path.read_bytes()).hexdigest() == want["csv"], key
        data = json.loads(truth_path.read_text())
        capital = {r["id"] for r in rows if r["description"] == "FPS CREDIT DIRECTOR CAPITAL INTRODUCED"}
        changed = {i for i, c in data["category_by_id"].items() if c == "director_funding"}
        assert changed == capital, key
        data["category_by_id"].update({i: "other" for i in changed})
        before = json.dumps(data, indent=2, default=str)
        assert hashlib.sha256(before.encode()).hexdigest() == want["truth"], key
        assert synth.generate(archetype, profile, seed=int(seed), months=int(months), end=END,
                              features=())[0] == rows


@pytest.mark.contracts
def test_generate_cli_writes_the_featured_case(tmp_path):
    r = CliRunner().invoke(app, ["generate", "--archetype", "trades", "--profile", "good", "--out", str(tmp_path),
                                 "--feature", "all"])
    assert r.exit_code == 0, r.output
    truth = json.loads((tmp_path / "trades_good.truth.json").read_text())
    assert truth["features"] == list(synth.FEATURES)
    assert truth["case_profile"]["own_accounts"] and truth["case_profile"]["directors"]
    assert "rows" not in truth["accounts"][0]
    assert ingest.load_csv(tmp_path / "trades_good_reserve.csv").transactions
    r = CliRunner().invoke(app, ["generate", "--out", str(tmp_path), "--feature", "cash", "--feature", "lenders"])
    assert r.exit_code == 0, r.output
    assert json.loads((tmp_path / "cafe_stretched.truth.json").read_text())["features"] == ["cash", "lenders"]
    r = CliRunner().invoke(app, ["generate", "--out", str(tmp_path), "--feature", "crypto"])
    assert r.exit_code == 2 and "unknown feature" in r.output


@pytest.mark.contracts
def test_evals_report_a_disagreement_with_feature_truth(tmp_path):
    """The evals' feature check is not vacuous: a truth that disagrees is reported."""
    sys.path.insert(0, str(ROOT / "evals"))
    try:
        import run_evals
    finally:
        sys.path.remove(str(ROOT / "evals"))
    rows, truth = synth.generate("cafe", "good", end=END, features=synth.FEATURES)
    csv_path, _ = synth.write(rows, truth, tmp_path, stem="case")
    assert run_evals.feature_mismatches(truth, csv_path) == []
    tid = truth.buckets[Bucket.DIRECTOR_FUNDING.value]["ids"][0]
    truth.category_by_id[tid] = Category.OTHER.value
    truth.indicators = truth.indicators[1:]
    truth.accounts[0]["buckets"][Bucket.OTHER_EXPLAINED_IN.value]["amount"] += 0.01
    found = run_evals.feature_mismatches(truth, csv_path)
    assert f"main:category:{tid}" in found and "main:indicators" in found and "reserve:M24" in found


@pytest.mark.invariants
@pytest.mark.parametrize("archetype", synth.ARCHETYPES)
@pytest.mark.parametrize("seed,months", [(7, 12), (11, 6), (5, 24)])
def test_p17_a_cash_deposit_of_exactly_the_limit_is_flagged_and_a_penny_under_is_not(tmp_path, archetype,
                                                                                    seed, months):
    """The single-deposit limit (£2,500.00, the shipped threshold) is 'at or above' (R14, R47):
    a generated deposit of exactly the limit is flagged, in the system and in the truth, and
    one a penny under is not, so P17 holds at the boundary, not only well above it."""
    _, accounts = _accounts(tmp_path, archetype, "stretched", ("cash",), seed, months)
    main, s = accounts[0]
    cash = {x.id: x.amount for x in s.transactions if x.category == Category.CASH_DEPOSIT}
    at = [i for i, a in cash.items() if a == 2500.00]
    under = [i for i, a in cash.items() if a == 2499.99]
    assert len(at) == 1 and len(under) == 1
    flagged = {i for x in indicators.raise_indicators(s) if x.kind == "single_cash_deposit" for i in x.evidence}
    truth = {i for x in main["indicators"] if x["kind"] == "single_cash_deposit" for i in x["lines"]}
    assert at[0] in flagged and at[0] in truth
    assert under[0] not in flagged and under[0] not in truth


@pytest.mark.invariants
@pytest.mark.parametrize("archetype", synth.ARCHETYPES)
def test_the_evals_report_a_one_penny_bucket_difference_whatever_the_amount(tmp_path, archetype):
    """Antigravity re-review of T10: the evals compared bucket amounts by abs(a - b) < 0.01 in
    binary floating point, which passes a 1p difference on about half of all amounts (£16,500.00
    against £16,500.01 among them). Every bucket and total, one penny off, is now reported."""
    sys.path.insert(0, str(ROOT / "evals"))
    try:
        import run_evals
    finally:
        sys.path.remove(str(ROOT / "evals"))
    rows, truth = synth.generate(archetype, "stretched", end=END, features=synth.FEATURES)
    csv_path, _ = synth.write(rows, truth, tmp_path, stem="case")
    assert run_evals.feature_mismatches(truth, csv_path) == []
    for bucket, want in truth.buckets.items():
        if not want["amount"]:
            continue
        want["amount"] += 0.01
        assert run_evals.feature_mismatches(truth, csv_path), (archetype, bucket)
        want["amount"] -= 0.01
    truth.total_money_in += 0.01
    assert run_evals.feature_mismatches(truth, csv_path)
