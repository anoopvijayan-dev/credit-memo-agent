"""T35: the two-account synthetic feature as one case, with own-account pair truth (R47, P17, P11).

synth.to_case imports a generated business account and its reserve account into one case,
with the case profile the generator gives, and truth.case holds the truth of that case as one:
each line's category under its case line id (account key prefixed, R41), the own-account pairs
the generator's moves make, the combined money-in buckets and cash deposits, the money in from
outside the applicant's accounts, and each account's indicators. The case pipeline
(overrides.case_figures, the pairing across accounts, the combined figures and the workspace
summary) must give exactly that truth (P17), and its pairs must be one debit and one credit of
equal amount in different accounts, no line in two pairs, with total money in less
own-account transfers in equal to the generator's money from outside the accounts (P11)."""

import json
import re
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import indicators, metrics, overrides, store, synth
from cma.cli import app
from cma.models import Bucket, Category
from cma.review import summary

END = date(2026, 9, 30)
PAIRING = "pairing:"
BUCKET_IDS = {Bucket.TRADING_RECEIPTS: "M2", **{b: mid for b, (mid, _, _) in metrics.BUCKET_METRICS.items()}}
CASES = [(a, p) for a in synth.ARCHETYPES for p in synth.PROFILES]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CMA_HOME", str(tmp_path / "cma-home"))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return tmp_path / "cma-home"


def _case(tmp_path, archetype, profile, features=synth.FEATURES, seed=7, months=12):
    rows, truth = synth.generate(archetype, profile, seed=seed, months=months, end=END, features=features)
    case_id, folder = synth.to_case(rows, truth, tmp_path / "work")
    figures = overrides.case_figures(folder)
    return truth, case_id, folder, figures, overrides.combined_figures(figures)


def _cents(v):
    return None if v is None else round(v * 100)


def _assert_p17(truth, figures, both):
    case = truth.case
    assert list(figures) == case["accounts"] == ["main", "reserve"]
    # Every line's category, under its case line id.
    assert {t.id: t.category.value for t in both.statement.transactions} == case["category_by_id"]
    # Every bucket, total money in and the cash within trading receipts, across the case.
    m = {x.id: x for x in both.metrics}
    assert _cents(m["M17"].value) == _cents(case["total_money_in"])
    for bucket, mid in BUCKET_IDS.items():
        want = case["buckets"][bucket.value]
        assert _cents(m[mid].value) == _cents(want["amount"]), mid
        assert sorted(m[mid].evidence) == sorted(want["ids"]), mid
    assert _cents(m["M18"].value) == _cents(case["cash_deposits"]["amount"])
    assert sorted(m["M18"].evidence) == sorted(case["cash_deposits"]["ids"])
    # Every indicator flag, raised on each account as the workspace raises them.
    for account, f in figures.items():
        got = [(i.kind, i.evidence) for i in indicators.raise_indicators(f.statement)]
        assert got == [(i["kind"], i["lines"]) for i in case["indicators"][account]], account


def _p11_pairs(both):
    """Each own-account pair is one debit and one credit of equal amount in different accounts,
    each naming the other, and no line is in two pairs. Returns the pairs, debit first."""
    lines = {t.id: t for t in both.statement.transactions}
    pairs, seen = set(), set()
    for cid, t in lines.items():
        if not (t.rule_id or "").startswith(PAIRING):
            continue
        other = lines[t.rule_id[len(PAIRING):]]
        assert other.rule_id == PAIRING + cid
        assert t.category == other.category == Category.OWN_ACCOUNT_TRANSFER
        assert other.account_key != t.account_key
        assert metrics.size_pence(other) == metrics.size_pence(t)
        assert (t.amount > 0) != (other.amount > 0)
        assert cid not in seen
        seen.add(cid)
        pairs.add((cid, other.id) if t.amount < 0 else (other.id, cid))
    return pairs


@pytest.mark.invariants
@pytest.mark.parametrize("archetype,profile", CASES)
def test_p17_generated_two_account_case_matches_its_case_truth(home, tmp_path, archetype, profile):
    truth, _, _, figures, both = _case(tmp_path, archetype, profile)
    _assert_p17(truth, figures, both)


@pytest.mark.invariants
@pytest.mark.parametrize("features", [("two_accounts",), ("director", "two_accounts"), ("cash", "two_accounts"),
                                      ("acquirers", "lenders", "two_accounts")])
@pytest.mark.parametrize("seed,months", [(3, 12), (11, 6), (5, 24)])
def test_p17_and_p11_any_two_account_feature_set_as_one_case(home, tmp_path, features, seed, months):
    for archetype in synth.ARCHETYPES:
        truth, _, _, figures, both = _case(tmp_path / archetype, archetype, "stretched", features, seed, months)
        _assert_p17(truth, figures, both)
        assert _p11_pairs(both) == {tuple(p) for p in truth.case["own_account_pairs"]}


@pytest.mark.invariants
@pytest.mark.parametrize("archetype,profile", CASES)
def test_p11_generated_two_account_case_pairs_every_move_and_leaves_outside_money(home, tmp_path, archetype,
                                                                                   profile):
    truth, _, _, figures, both = _case(tmp_path, archetype, profile)
    pairs = _p11_pairs(both)
    # The pairs are exactly the generator's moves between the two accounts.
    assert pairs == {tuple(p) for p in truth.case["own_account_pairs"]}
    assert pairs, "the two-account feature moves money both ways"
    debits = {d.split(":")[0] for d, _ in pairs}
    assert debits == {"main", "reserve"}
    # Total money in less own-account transfers in is the generator's money from outside.
    m = {x.id: x for x in both.metrics}
    assert _cents(m["M17"].value) - _cents(m["M19"].value) == _cents(truth.case["outside_money_in"])
    # Every own-account transfer in the case is half of a pair: the pairing settles them,
    # above the profile's names, and no debit is left with two candidates.
    moves = {t.id for t in both.statement.transactions if t.category == Category.OWN_ACCOUNT_TRANSFER}
    assert moves == {i for p in pairs for i in p}
    assert next(iter(figures.values())).possible_pairs == []


@pytest.mark.invariants
def test_p11_outside_money_is_the_credits_of_no_move(home, tmp_path):
    """The truth's outside money is worked out from the generator's moves, not from the
    categories the system is checked against: every credit not half of a move."""
    rows, truth = synth.generate("cafe", "good", end=END, features=synth.FEATURES)
    case = truth.case
    in_pairs = {i for p in case["own_account_pairs"] for i in p}
    lines = [(f"main:{r['id']}", r["amount"]) for r in rows]
    lines += [(f"reserve:{r['id']}", r["amount"]) for r in truth.accounts[0]["rows"]]
    outside = sum(synth._pence(a) for i, a in lines if not a.startswith("-") and i not in in_pairs)
    assert round(case["outside_money_in"] * 100) == outside
    assert case["buckets"][Bucket.OWN_ACCOUNT_IN.value]["ids"] == sorted(
        (i for i, a in lines if i in in_pairs and not a.startswith("-")),
        key=[i for i, _ in lines].index)


@pytest.mark.contracts
def test_to_case_imports_both_accounts_with_the_generated_profile(home, tmp_path):
    rows, truth = synth.generate("trades", "good", end=END, features=synth.FEATURES)
    case_id, folder = synth.to_case(rows, truth, tmp_path / "work", label="synthetic trades")
    profile = store.load_profile(folder)
    assert profile["demo"] is True and profile["label"] == "synthetic trades"
    assert profile["directors"] == truth.case_profile["directors"]
    assert profile["own_accounts"] == truth.case_profile["own_accounts"]
    assert profile["vat_stagger"] == truth.vat_stagger
    s = summary.case_summary(folder, case_id)
    assert [a["account"] for a in s["accounts"]] == ["main", "reserve"]
    assert s["combined"] is not None
    assert not any("possible_own_account_transfer" in e["reasons"] for e in s["queue"])


@pytest.mark.contracts
def test_a_single_account_feature_set_has_no_case_truth(home, tmp_path):
    rows, truth = synth.generate("cafe", "good", end=END, features=("cash",))
    assert truth.case == {}
    assert "case" not in truth.to_json()
    _, folder = synth.to_case(rows, truth, tmp_path / "work")
    assert list(overrides.case_figures(folder)) == ["main"]


@pytest.mark.contracts
def test_generate_cli_makes_the_two_account_case(home, tmp_path):
    r = CliRunner().invoke(app, ["generate", "--archetype", "ecommerce", "--profile", "good", "--out", str(tmp_path),
                                 "--feature", "all", "--case"])
    assert r.exit_code == 0, r.output
    truth = json.loads((tmp_path / "ecommerce_good.truth.json").read_text())
    assert truth["case"]["accounts"] == ["main", "reserve"] and truth["case"]["own_account_pairs"]
    case_id = re.search(r"made case ([0-9a-f]+)", r.output).group(1)
    folder = store.case_folder(case_id)
    both = overrides.combined_figures(overrides.case_figures(folder))
    assert {t.id: t.category.value for t in both.statement.transactions} == truth["case"]["category_by_id"]


def _cases(home):
    d = home / "cases"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


@pytest.mark.contracts
def test_to_case_removes_the_case_it_made_when_an_import_fails(home, tmp_path, monkeypatch):
    """Partial failure: an import refused after the case is made leaves no half-imported case."""
    rows, truth = synth.generate("cafe", "good", end=END, features=synth.FEATURES)
    real, calls = store.import_file, []

    def failing(folder, src, fmt, **kw):
        calls.append(kw["account"])
        if kw["account"] == "reserve":
            raise store.CaseStoreError("import refused: test")
        return real(folder, src, fmt, **kw)

    monkeypatch.setattr(store, "import_file", failing)
    with pytest.raises(store.CaseStoreError):
        synth.to_case(rows, truth, tmp_path / "work")
    assert calls == ["main", "reserve"]
    assert _cases(home) == []
    monkeypatch.setattr(store, "import_file", real)
    case_id, _ = synth.to_case(rows, truth, tmp_path / "work")  # a second run makes the case whole
    assert _cases(home) == [case_id]


@pytest.mark.contracts
def test_generate_cli_case_log_refusal_is_said_without_a_traceback(home, tmp_path, monkeypatch):
    from cma import case as caselog

    def locked(*a, **kw):
        raise caselog.CaseLocked("another window or command is writing to this case")

    monkeypatch.setattr(store, "import_file", locked)
    r = CliRunner().invoke(app, ["generate", "--archetype", "cafe", "--profile", "good", "--out", str(tmp_path),
                                 "--feature", "all", "--case"])
    assert r.exit_code == 4, r.output
    assert "Not recorded." in r.output and "Traceback" not in r.output
    assert _cases(home) == []


@pytest.mark.contracts
def test_generate_cli_case_in_a_sync_folder_warns_on_stderr_first(home, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "sync_folder", lambda p, **kw: store.SyncFolder("OneDrive", tmp_path))
    r = CliRunner().invoke(app, ["generate", "--archetype", "cafe", "--profile", "good",
                                                 "--out", str(tmp_path), "--feature", "all", "--case"])
    assert r.exit_code == 0, r.output
    assert "Warning:" in r.stderr and "OneDrive" in r.stderr
