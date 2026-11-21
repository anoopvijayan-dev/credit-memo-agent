"""T21: the workspace's policy-check panel (C5; R15, R24, R25, R56, P20).

While show_policy_verdict is on in firm.yaml (the default), the case view shows, for each
account, the overall pass, refer or fail outcome and each rule's result from the packaged
policy, computed by policy.evaluate from the current figures (overrides included), with the
illustrative label inside the same panel (R25). The recommendation is a field left blank for
the underwriter whatever the outcome (R24). With show_policy_verdict off, neither the page nor
the API holds an outcome or a rule result (R56).

Each test talks to a real server running in a thread, through its page (/) and API (/api/case)."""

import http.client
import json
import re
from html import escape
from html.parser import HTMLParser

import pytest

from cma import overrides, policy, store
from cma.models import RuleOutcome
from cma.review import server as rs

STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
             "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.25\n"
             "L3,2026-01-07,DD COUNCIL,-100.00,1700.25\n"
             "L4,2026-02-02,CASH PAID IN,250.10,1950.35\n")
OTHER = ("id,date,description,amount,balance\n"
         "S1,2026-01-03,CARD SETTLEMENT SUMUP,900.00,900.00\n"
         "S2,2026-01-20,IWOCA LOAN REPAYMENT,-120.00,780.00\n")
OUTCOME_WORD = re.compile(r"\b(pass|refer|fail|PASS|REFER|FAIL)\b")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _setting(home, shown: bool | None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    if shown is not None:
        (home / "firm.yaml").write_text(f"show_policy_verdict: {'true' if shown else 'false'}\n", encoding="utf-8")


def _case(tmp_path, accounts=(("main", STATEMENT),)):
    case_id = store.new_case(label="t21")
    folder = store.case_folder(case_id)
    for account, body in accounts:
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
        return r.status, r.read()
    finally:
        conn.close()


def _page(srv) -> str:
    status, body = _get(srv, "/")
    assert status == 200
    return body.decode("utf-8")


def _api(srv) -> dict:
    status, body = _get(srv, "/api/case")
    assert status == 200
    return json.loads(body)


class _Sections(HTMLParser):
    """Each element's text, kept with the classes and ids of the sections around it."""

    def __init__(self):
        super().__init__()
        self.stack: list[dict] = []
        self.done: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.stack.append({"tag": tag, "class": (a.get("class") or "").split(), "id": a.get("id"),
                           "data": a, "text": []})

    def handle_endtag(self, tag):
        while self.stack:
            el = self.stack.pop()
            text = "".join(el["text"])
            el["text"] = text
            self.done.append(el)
            if self.stack:
                self.stack[-1]["text"].append(text)
            if el["tag"] == tag:
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"].append(data)


def _elements(page: str) -> list[dict]:
    p = _Sections()
    p.feed(page)
    p.close()
    return p.done


def _expected(folder):
    """The policy check the workspace must show: policy.evaluate on each account's current figures."""
    return {a: policy.evaluate(f.metrics) for a, f in overrides.case_figures(folder).items()}


def _check_shown(page: str, folder) -> None:
    els = _elements(page)
    panels = [e for e in els if "policy-check" in e["class"]]
    expected = _expected(folder)
    assert len(panels) == len(expected) >= 1
    for panel in panels:
        # P20: the panel that shows an outcome holds the illustrative label.
        assert policy.ILLUSTRATIVE_LABEL in panel["text"]
    for account, result in expected.items():
        panel = next(p for p in panels if p["data"].get("data-account") == account)
        assert f"Overall outcome: {result.overall.value.upper()}" in panel["text"]
        for r in result.rules:
            assert r.rule_id in panel["text"] and r.description in panel["text"] and r.detail in panel["text"]
    # Every element that shows an outcome sits inside a panel with the label (P20).
    for e in els:
        if "outcome" in e["class"] or "rule-outcome" in e["class"]:
            assert any(p["text"].find(e["text"]) >= 0 and policy.ILLUSTRATIVE_LABEL in p["text"] for p in panels)
    # The recommendation is left blank for the underwriter (R24, P20).
    rec = [e for e in els if e["data"].get("data-field") == "recommendation"]
    assert len(rec) == 1 and rec[0]["text"].strip() == ""


def _check_hidden(page: str, folder) -> None:
    els = _elements(page)
    assert not [e for e in els if "policy-check" in e["class"] or "outcome" in e["class"]
                or "rule-outcome" in e["class"]]
    assert "Overall outcome" not in page and policy.ILLUSTRATIVE_LABEL not in page
    for result in _expected(folder).values():
        for r in result.rules:  # no rule result: neither its sentence nor its rule
            for text in (r.detail, r.description):
                assert text not in page and escape(text, quote=True) not in page
            assert f">{r.rule_id}<" not in page
    assert not OUTCOME_WORD.search(page)
    # The recommendation is still blank for the underwriter (R24).
    rec = [e for e in els if e["data"].get("data-field") == "recommendation"]
    assert len(rec) == 1 and rec[0]["text"].strip() == ""


@pytest.mark.invariants
@pytest.mark.parametrize("setting", [None, True, False], ids=["default", "on", "off"])
@pytest.mark.parametrize("accounts", [(("main", STATEMENT),), (("main", STATEMENT), ("savings", OTHER))],
                         ids=["one-account", "two-accounts"])
def test_p20_the_workspace_html_under_both_settings(home, tmp_path, serve, setting, accounts):
    _setting(home, setting)
    case_id, folder = _case(tmp_path, accounts)
    page = _page(serve(case_id))
    if setting is False:
        _check_hidden(page, folder)
    else:  # on, and on by default (R25)
        _check_shown(page, folder)


@pytest.mark.invariants
def test_p20_the_api_holds_no_outcome_or_rule_result_with_the_setting_off(home, tmp_path, serve):
    _setting(home, False)
    case_id, folder = _case(tmp_path)
    data = _api(serve(case_id))
    assert data["policy_check"] == {"shown": False}
    text = json.dumps(data)
    for result in _expected(folder).values():
        assert '"overall"' not in text
        for r in result.rules:
            assert r.detail not in text and r.description not in text


@pytest.mark.contracts
def test_the_api_gives_each_account_s_policy_check_with_the_label(home, tmp_path, serve):
    _setting(home, None)
    case_id, folder = _case(tmp_path)
    data = _api(serve(case_id))
    expected = _expected(folder)["main"]
    assert data["policy_check"]["shown"] is True
    assert data["policy_check"]["label"] == policy.ILLUSTRATIVE_LABEL
    pc = data["accounts"][0]["policy_check"]
    assert pc["overall"] == expected.overall.value
    assert pc["policy_name"] == expected.policy_name and pc["policy_sha256"] == expected.policy_sha256
    assert [(r["rule_id"], r["outcome"], r["detail"]) for r in pc["rules"]] == [
        (r.rule_id, r.outcome.value, r.detail) for r in expected.rules]
    assert data["recommendation"] is None


@pytest.mark.contracts
def test_the_policy_check_follows_the_current_figures_after_an_override(home, tmp_path, serve):
    """An override moves a line, the figures recompute, and the panel shows the outcome for the
    figures as they now stand (R25), never one computed before."""
    _setting(home, None)
    case_id, folder = _case(tmp_path)
    before = _expected(folder)["main"]
    overrides.add_override(folder, "L1", "other", reason="not trading", name="A Person")
    after = _expected(folder)["main"]
    assert [r.detail for r in before.rules] != [r.detail for r in after.rules]
    srv = serve(case_id)
    _check_shown(_page(srv), folder)
    pc = _api(srv)["accounts"][0]["policy_check"]
    assert [r["detail"] for r in pc["rules"]] == [r.detail for r in after.rules]


@pytest.mark.contracts
def test_the_label_has_no_digits_and_says_the_underwriter_decides():
    assert not re.search(r"\d", policy.ILLUSTRATIVE_LABEL)
    assert "not your firm's lending policy" in policy.ILLUSTRATIVE_LABEL
    assert "the underwriter decides" in policy.ILLUSTRATIVE_LABEL


@pytest.mark.contracts
def test_a_case_with_no_figures_shows_no_policy_check(home, tmp_path, serve):
    _setting(home, None)
    case_id = store.new_case(label="empty")
    page = _page(serve(case_id))
    assert "policy-check" not in page and not OUTCOME_WORD.search(page)


@pytest.mark.contracts
def test_outcome_values_are_the_three_policy_outcomes():
    assert {o.value for o in RuleOutcome} == {"pass", "refer", "fail"}
