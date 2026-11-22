"""Firm configuration and the offline lock (R29, R30, R34; properties P18 and P5)."""

import asyncio
import contextlib
import json
import re
import socket
import sys
import types
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import firm, llm, synth
from cma.cli import app
from cma.graph import REDRAFT_LABEL, build, draft_prompt, evidence_record


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("CMA_HOME", str(root))
    return root


@pytest.fixture
def no_network(monkeypatch):
    """Any attempt to open a socket is recorded and fails."""
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    return attempts


# --- firm.yaml -------------------------------------------------------------------

def test_defaults_without_a_file(home):
    cfg = firm.load()
    assert cfg.offline_lock is True and cfg.show_policy_verdict is True
    assert cfg.root == home
    assert cfg.firm_rules_path == home / "firm_rules.yaml"
    assert cfg.thresholds_path == home / "indicators.yaml"
    assert cfg.memo_fields_path == home / "memo_fields.yaml"


def test_root_defaults_to_private_cma(tmp_path, monkeypatch):
    monkeypatch.delenv("CMA_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    assert firm.load().root == (tmp_path / "private" / "cma").resolve()


def test_file_sets_flags_and_paths(home):
    (home / "firm.yaml").write_text(
        "offline_lock: false\nshow_policy_verdict: false\n"
        "paths:\n  firm_rules: rules/mine.yaml\n  thresholds: /abs/ind.yaml\n"
    )
    cfg = firm.load()
    assert cfg.offline_lock is False and cfg.show_policy_verdict is False
    assert cfg.firm_rules_path == home / "rules" / "mine.yaml"
    assert str(cfg.thresholds_path) == "/abs/ind.yaml"
    assert cfg.memo_fields_path == home / "memo_fields.yaml"


@pytest.mark.parametrize("text, problem", [
    ("offline_lock: 0\n", "offline_lock must be true or false"),
    ("offline_lock: 'no'\n", "offline_lock must be true or false"),
    ("show_policy_verdict: maybe\n", "show_policy_verdict must be true or false"),
    ("timing: true\n", "unknown key 'timing'"),
    ("paths:\n  memo: x.yaml\n", "unknown path 'memo'"),
    ("paths:\n  thresholds: 3\n", "paths.thresholds must be a file path"),
    ("offline_lock: false\npaths: []\n", "paths must be a mapping"),
    ("offline_lock: false\npaths: 0\n", "paths must be a mapping"),
    ("offline_lock: false\npaths: ''\n", "paths must be a mapping"),
    ("- offline_lock\n", "not a mapping"),
    ("offline_lock: [\n", "not valid YAML"),
    ("offline_lock: true\noffline_lock: false\n", "key 'offline_lock' is given more than once"),
    ("paths:\n  thresholds: a.yaml\n  thresholds: b.yaml\n", "key 'thresholds' is given more than once"),
    ("? [a]\n: 1\n", "not valid YAML"),
])
def test_malformed_file_is_refused(home, text, problem):
    # A malformed lock must never fall back silently to some other setting.
    (home / "firm.yaml").write_text(text)
    with pytest.raises(firm.FirmConfigError, match=r"firm\.yaml") as e:
        firm.load()
    assert problem in str(e.value)


@pytest.mark.parametrize("raw", [b"offline_lock: false\n# \xff\xfe\n", b"\xff\xfeo\x00f\x00"])
def test_firm_file_that_is_not_utf8_is_refused(home, raw):
    (home / "firm.yaml").write_bytes(raw)
    with pytest.raises(firm.FirmConfigError, match="UTF-8"):
        firm.load()


def test_cli_refuses_a_firm_file_that_is_not_utf8(home, tmp_path):
    (home / "firm.yaml").write_bytes(b"offline_lock: false\n# \xff\n")
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic"])
    assert r.exit_code == 2, r.output
    assert "Refused." in r.output and "UTF-8" in r.output


def test_empty_file_means_defaults(home):
    (home / "firm.yaml").write_text("")
    assert firm.load().offline_lock is True


# --- P18: under the lock, hosted models and the MCP server fail before any network call

LOCK_ON = [None, "offline_lock: true\n", "show_policy_verdict: false\n"]


@pytest.fixture
def sdks(monkeypatch):
    """Stand-in anthropic and openai modules that record every client made and every request
    sent, so these tests run whether or not the llm extra is installed."""
    calls = []

    ns = types.SimpleNamespace

    class Client:
        def __init__(self, sdk, **kwargs):
            calls.append((sdk, "client"))
            self.messages = ns(create=lambda **k: calls.append((sdk, "request")) or ns(
                stop_reason="end_turn", content=[ns(type="text", text="ok")]))
            self.chat = ns(completions=ns(create=lambda **k: calls.append((sdk, "request")) or ns(
                choices=[ns(finish_reason="stop", message=ns(content="ok"))])))

    anthropic = types.ModuleType("anthropic")
    anthropic.Anthropic = lambda **k: Client("anthropic", **k)
    openai = types.ModuleType("openai")
    openai.OpenAI = lambda **k: Client("openai", **k)
    monkeypatch.setitem(sys.modules, "anthropic", anthropic)
    monkeypatch.setitem(sys.modules, "openai", openai)
    return calls


@pytest.mark.parametrize("firm_yaml", LOCK_ON)
@pytest.mark.parametrize("provider", ["anthropic", "openai", "Anthropic", "OPENAI"])
@pytest.mark.invariants
def test_p18_hosted_model_refused_under_lock(home, no_network, sdks, monkeypatch, firm_yaml, provider):
    if firm_yaml is not None:
        (home / "firm.yaml").write_text(firm_yaml)
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        llm.get_llm(provider)
    monkeypatch.setenv("CMA_LLM", provider)
    with pytest.raises(firm.OfflineLockError):
        llm.get_llm()
    for cls in (llm.AnthropicLLM, llm.OpenAILLM):
        with pytest.raises(firm.OfflineLockError):
            cls()
    assert sdks == [] and no_network == []


@pytest.mark.parametrize("cls", [llm.AnthropicLLM, llm.OpenAILLM])
@pytest.mark.invariants
def test_p18_existing_client_refused_once_the_lock_is_on(home, no_network, sdks, tmp_path, cls):
    # A client made while the lock was off must not send anything after the firm turns it on.
    (home / "firm.yaml").write_text("offline_lock: false\n")
    model = cls()
    assert model.complete("s", "u") == "ok"
    assert [c for c in sdks if c[1] == "request"] == [(model.name, "request")]
    sdks.clear()

    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        model.complete("s", "u")
    graph = build(model)
    with pytest.raises(firm.OfflineLockError):
        graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    (home / "firm.yaml").unlink()  # no file is the default: locked
    with pytest.raises(firm.OfflineLockError):
        model.complete("s", "u")
    assert sdks == [] and no_network == []


def test_offline_model_is_allowed_under_lock(home, no_network):
    assert llm.get_llm("fake").name == "fake"
    assert no_network == []


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_hosted_model_allowed_when_lock_is_off(home, no_network, sdks, provider):
    (home / "firm.yaml").write_text("offline_lock: false\n")
    assert llm.get_llm(provider).name == provider
    assert sdks == [(provider, "client")] and no_network == []


@pytest.mark.invariants
def test_p18_malformed_firm_file_refuses_hosted_model(home, no_network, sdks):
    (home / "firm.yaml").write_text("offline_lock: off-ish\n")
    with pytest.raises(firm.FirmConfigError):
        llm.get_llm("anthropic")
    assert sdks == [] and no_network == []


@pytest.mark.parametrize("firm_yaml", LOCK_ON)
@pytest.mark.invariants
def test_p18_mcp_server_refuses_to_start_under_lock(home, no_network, monkeypatch, tmp_path, firm_yaml):
    mcp_server = pytest.importorskip("cma.mcp_server")
    if firm_yaml is not None:
        (home / "firm.yaml").write_text(firm_yaml)
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    started = []
    monkeypatch.setattr(mcp_server.server, "run", lambda *a, **k: started.append(True))
    with pytest.raises(SystemExit) as e:
        mcp_server.main()
    assert "offline lock" in str(e.value.code)
    assert started == [] and no_network == []


@pytest.fixture
def mcp_parent(monkeypatch):
    """The SDK's own server start-up, recorded instead of run."""
    pytest.importorskip("mcp")
    from mcp.server.mcpserver import MCPServer

    started = []
    for name in ("run", "sse_app", "streamable_http_app"):
        monkeypatch.setattr(MCPServer, name, lambda *a, _n=name, **k: started.append(_n))
    for name in ("run_stdio_async", "run_sse_async", "run_streamable_http_async"):
        async def record(*a, _n=name, **k):
            started.append(_n)
        monkeypatch.setattr(MCPServer, name, record)
    return started


START_PATHS = [
    lambda s: s.run(),
    lambda s: s.run("sse"),
    lambda s: s.run("streamable-http"),
    lambda s: asyncio.run(s.run_stdio_async()),
    lambda s: asyncio.run(s.run_sse_async()),
    lambda s: asyncio.run(s.run_streamable_http_async()),
    lambda s: s.sse_app(),
    lambda s: s.streamable_http_app(),
]


@pytest.mark.parametrize("firm_yaml", LOCK_ON)
@pytest.mark.parametrize("start", START_PATHS)
@pytest.mark.invariants
def test_p18_exported_mcp_server_refuses_to_start_under_lock(home, no_network, mcp_parent, monkeypatch, tmp_path,
                                                         firm_yaml, start):
    # An embedding script can import the server object and start it without main().
    from cma import mcp_server

    if firm_yaml is not None:
        (home / "firm.yaml").write_text(firm_yaml)
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        start(mcp_server.server)
    assert mcp_parent == [] and no_network == []


@pytest.mark.parametrize("start", START_PATHS[:6])  # the run methods; no ASGI app is handed out
def test_exported_mcp_server_starts_when_lock_is_off(home, mcp_parent, monkeypatch, tmp_path, start):
    from cma import mcp_server

    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    start(mcp_server.server)
    assert len(mcp_parent) == 1


@pytest.mark.parametrize("tool, args", [
    ("statement_summary", ("x.csv",)), ("compute_metrics", ("x.csv",)),
    ("check_policy", ("x.csv",)), ("transactions_for", ("x.csv", ["T00001"])),
])
@pytest.mark.invariants
def test_p18_mcp_tools_refuse_under_lock(home, no_network, monkeypatch, tmp_path, tool, args):
    # However the server was started, no tool hands over data while the lock is on.
    mcp_server = pytest.importorskip("cma.mcp_server")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    with pytest.raises(firm.OfflineLockError):
        getattr(mcp_server, tool)(*args)
    assert no_network == []


def test_mcp_server_starts_when_lock_is_off(home, monkeypatch, tmp_path):
    mcp_server = pytest.importorskip("cma.mcp_server")
    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    started = []
    monkeypatch.setattr(mcp_server.server, "run", lambda *a, **k: started.append(True))
    mcp_server.main()
    assert started == [True]


def _statement(tmp_path, profile="stretched"):
    rows, truth = synth.generate("cafe", profile, end=date(2026, 9, 30))
    csv_path, _ = synth.write(rows, truth, tmp_path)
    return csv_path


@pytest.mark.invariants
def test_p18_cli_refuses_hosted_model_under_lock(home, no_network, tmp_path):
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic"])
    assert r.exit_code == 2, r.output
    assert "offline lock" in r.output
    assert no_network == []


@pytest.mark.invariants
def test_p18_a_lock_turned_on_before_the_graph_starts_is_the_runs_lock(home, no_network, sdks, monkeypatch,
                                                                        tmp_path):
    # verify-09 kept the CLI's first reading for the whole run, so a lock turned on after the
    # client was made and before the graph started still let the draft be sent. A reading
    # now becomes a run's only while firm.yaml is unchanged since it (T17 review 7): the run
    # reads firm.yaml at its start, finds the lock on, and the request is refused before it
    # is sent; nothing leaves the machine.
    (home / "firm.yaml").write_text("offline_lock: false\n")
    real_init = llm.AnthropicLLM.__init__

    def init_then_lock(self, *a, **k):
        real_init(self, *a, **k)
        (home / "firm.yaml").write_text("offline_lock: true\n")

    monkeypatch.setattr(llm.AnthropicLLM, "__init__", init_then_lock)
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: llm.FakeLLM().complete(s, u))
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic", "--yes",
                                 "--reviewer", "A", "--out", str(tmp_path / "memo.md")])
    assert r.exit_code == 2 and "Refused" in r.output and "offline lock" in r.output, r.output
    assert [c for c in sdks if c[1] == "request"] == [] and no_network == []
    assert not (tmp_path / "memo.md").exists()


@pytest.mark.invariants
def test_p18_graph_refuses_a_hosted_request_under_the_runs_lock(home, no_network, sdks, tmp_path):
    # A client made while the lock was off, in a run whose reading of firm.yaml has the lock
    # on: the request is refused before it is sent, and nothing is drafted.
    (home / "firm.yaml").write_text("offline_lock: false\n")
    model = llm.AnthropicLLM()
    (home / "firm.yaml").write_text("offline_lock: true\n")
    graph = build(model, firm_config=firm.load())
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    assert [c for c in sdks if c[1] == "request"] == [] and no_network == []


AT_RUN_START = "at run start; every hosted request in the run was checked against this value"


@pytest.mark.invariants
def test_p18_evidence_records_offline_mode(home, tmp_path):
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": True, "destination": "offline", "recorded": AT_RUN_START,
                                "resume_offline_lock": True}  # the review's resume (T17 review 10)


def test_evidence_records_lock_off_with_offline_model(home, tmp_path):
    (home / "firm.yaml").write_text("offline_lock: false\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": False, "destination": "offline", "recorded": AT_RUN_START,
                                "resume_offline_lock": False}  # the review's resume (T17 review 10)


@pytest.mark.parametrize("model, destination", [("fake:offline-template", "offline"),
                                                ("anthropic:claude-sonnet-5-5", "anthropic:claude-sonnet-5-5")])
def test_evidence_record_names_egress_when_state_has_no_lock_fields(home, tmp_path, model, destination):
    # A state with no lock fields (as a review checkpointed before the lock would hold) still
    # gets an egress entry; this proves the record's formatting, not a resume from an old file.
    graph = build(llm.FakeLLM())
    cfg = {"configurable": {"thread_id": "old"}}
    graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    state = dict(graph.get_state(cfg).values)
    for key in ("offline_lock", "egress"):
        state.pop(key)
    state["model"] = model
    state["review"] = None
    record = evidence_record(state)
    assert record["egress"] == {"offline_lock": False, "destination": destination,
                                "recorded": "at resume; the draft predates the offline lock"}


# --- P5: a prompt carries only the tables, the period and the placeholder name ----

SENTINELS = ["SNTLDESC", "SNTLNAME", "SNTLPATH", "SNTLBIZ", "SNTLDIR", "SNTLSHR", "SNTLACCT", "SNTLLABEL",
             "SNTLNOTE", "SNTLOVR"]


class RecordingLLM(llm.FakeLLM):
    """The offline model, keeping every prompt it is given."""

    def __init__(self, hallucinate=False):
        super().__init__(hallucinate=hallucinate)
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        return super().complete(system, user)


def _sentinel_case(home, profile, archetype):
    """A case folder as the case store lays it out: a profile whose every text field is a
    sentinel, an override log with a sentinel reason, and a real statement in which every
    description carries a sentinel and the profile's names, under a path that does too."""
    case = home / "cases" / "SNTLPATH-case"
    (case / "statements").mkdir(parents=True)
    profile_fields = {
        "label": "SNTLLABEL", "demo": False, "business_name": "SNTLBIZ TRADING LTD",
        "directors": ["SNTLDIR ONE"], "shareholders": ["SNTLSHR TWO"], "own_accounts": ["SNTLACCT 12345678"],
        "vat_stagger": 2, "proposed_monthly_repayment": 1500.00, "notes": "SNTLNOTE",
    }
    import yaml
    (case / "case.yaml").write_text(yaml.safe_dump(profile_fields))
    (case / "overrides.jsonl").write_text(json.dumps({"line": "T00001", "reason": "SNTLOVR"}) + "\n")
    rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
    names = ["SNTLBIZ", "SNTLDIR ONE", "SNTLSHR TWO", "SNTLACCT 12345678"]
    for i, row in enumerate(rows):
        # Prefix and suffix, so rules that read either end still see payer text around the sentinel.
        extra = f"SNTLDESC{i:05d} {names[i % len(names)]}"
        row["description"] = f"{row['description']} {extra}" if i % 2 else f"{extra} {row['description']}"
    csv_path, _ = synth.write(rows, truth, case / "statements", stem="SNTLNAME account")
    return csv_path, profile_fields


@pytest.mark.parametrize("archetype", list(synth.ARCHETYPES))
@pytest.mark.parametrize("profile", list(synth.PROFILES))
@pytest.mark.parametrize("hallucinate", [False, True])
@pytest.mark.invariants
def test_p5_no_prompt_carries_line_level_text(home, monkeypatch, archetype, profile, hallucinate):
    from cma import graph as graph_module
    from cma import ingest

    path, case_profile = _sentinel_case(home, profile, archetype)
    real_load = ingest.load_csv
    # The statement in the graph carries the account's real (sentinel) name, as an import would.
    monkeypatch.setattr(graph_module.ingest, "load_csv", lambda p, **k: real_load(p, account_name="SNTLNAME LTD"))
    model = RecordingLLM(hallucinate=hallucinate)
    graph = build(model)
    cfg = {"configurable": {"thread_id": f"{archetype}-{profile}-{hallucinate}"}}
    state = graph.invoke({"statement_path": str(path), "vat_stagger": case_profile["vat_stagger"]}, cfg)
    assert state["statement"].account_name == "SNTLNAME LTD"
    assert model.prompts
    if hallucinate:
        assert len(model.prompts) > 1  # the redraft path ran
    first_system, first_user = model.prompts[0]
    for i, (system, user) in enumerate(model.prompts):
        for sentinel in SENTINELS:
            assert sentinel not in system + user
        assert "48,250" not in user  # the model's own invented figure is never sent back
        assert system == first_system
        # A redraft prompt is the first prompt plus one line of metric and rule ids: no model
        # output, no diagnostics.
        if i == 0:
            assert user == first_user
        else:
            assert user.startswith(first_user)
            assert REDRAFT_IDS.fullmatch(user[len(first_user):]), user[len(first_user):]
    _assert_only_tables_period_placeholder(first_user)


REDRAFT_IDS = re.compile(r"\n" + re.escape(REDRAFT_LABEL) + r" (none|[MR]\d+(, [MR]\d+)*)\n")


def _assert_only_tables_period_placeholder(user, policy_name=None):
    assert "Account: Applicant\n" in user
    assert "Metrics table:" in user and "Policy table:" in user
    assert "Overall policy outcome" not in user and "Policy: " not in user
    if policy_name:
        assert policy_name not in user
    lines = user.splitlines()
    for line in lines:
        assert (not line or line.startswith(("Account: Applicant", "Period: ", "Metrics table:", "Policy table:",
                                               REDRAFT_LABEL + " ", "|")))
    # The overall outcome is a row of the rule table.
    assert any(re.fullmatch(r"\| overall \| overall outcome \| - \| - \| (pass|refer|fail) \| - \|", x) for x in lines)


@pytest.mark.invariants
def test_p5_prompt_uses_placeholder_not_the_account_name(home, tmp_path):
    from cma import categorise, ingest, metrics, policy

    st = ingest.load_csv(_statement(tmp_path), account_name="SNTLNAME HOLDINGS LTD")
    st = categorise.categorise(st)
    ms = metrics.compute(st)
    p = policy.evaluate(ms)
    for redraft in (None, [], ["M3", "R2"]):
        user = draft_prompt(st, ms, p, redraft=redraft)
        assert "SNTLNAME" not in user
        assert f"Period: {st.start} to {st.end}\n" in user
        _assert_only_tables_period_placeholder(user, policy_name=p.policy_name)
        if redraft is not None:
            assert REDRAFT_IDS.search(user)


@pytest.mark.invariants
def test_p5_redraft_feedback_is_only_ids_from_the_tables(home, tmp_path):
    # The guard's findings quote the model's own output; only metric and rule ids that are in
    # the tables go back to the model, never a number or a tag the model made up.
    from cma import categorise, guard, ingest, metrics, policy
    from cma.graph import redraft_ids

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path)))
    ms = metrics.compute(st)
    p = policy.evaluate(ms)
    memo = llm.FakeLLM(hallucinate=True).complete("", draft_prompt(st, ms, p))
    memo += "\nCover was 9.99x [M10] and SNTLDESC00001 paid 3.21 [M99]; the limit is 0.01x [R2]. M3 grew.\n"
    g = guard.check(memo, ms, p, required_headings=guard.REQUIRED_HEADINGS)
    ids = redraft_ids(g, ms, p)
    assert "M10" in ids and "R2" in ids and "M99" not in ids
    user = draft_prompt(st, ms, p, redraft=ids)
    addition = user[len(draft_prompt(st, ms, p)):]
    assert REDRAFT_IDS.fullmatch(addition)
    for leaked in ("9.99", "3.21", "48,250", "SNTLDESC", "M99"):
        assert leaked not in addition


# --- P18: retries and a saved ASGI app (verify-09) ----------------------------------

@pytest.mark.invariants
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_p18_hosted_sdk_sends_one_attempt_per_lock_check(home, monkeypatch, provider):
    # The SDK is built with its own retries off, so a retryable failure (503 asking for a
    # retry) sends exactly one HTTP request per `complete`, and each was checked against the
    # lock. With the lock then turned on, the next attempt is refused before it is sent.
    sdk = pytest.importorskip(provider)
    import importlib

    # Each SDK takes the HTTP client of the package it is built on (httpx, or httpx2).
    base = sdk._base_client
    httpx = importlib.import_module("httpx2" if "httpx2" in base.__dict__ else "httpx")

    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(503, headers={"retry-after": "0", "x-should-retry": "true"},
                              json={"error": {"type": "overloaded_error", "message": "busy"}})

    real = sdk.Anthropic if provider == "anthropic" else sdk.OpenAI
    built = []

    def make(**kwargs):
        built.append(kwargs)
        return real(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)

    monkeypatch.setattr(sdk, "Anthropic" if provider == "anthropic" else "OpenAI", make)
    (home / "firm.yaml").write_text("offline_lock: false\n")
    model = llm.get_llm(provider)
    assert built[0]["max_retries"] == 0
    with pytest.raises(Exception) as e:
        model.complete("s", "u")
    assert not isinstance(e.value, firm.OfflineLockError)
    assert len(sent) == 1  # no retry inside the SDK

    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        model.complete("s", "u")
    with pytest.raises(firm.OfflineLockError):
        model.complete("s", "u", offline_lock=True)
    assert len(sent) == 1


@pytest.mark.invariants
@pytest.mark.parametrize("factory", ["streamable_http_app", "sse_app"])
def test_p18_an_asgi_app_is_refused_under_the_lock(home, no_network, monkeypatch, tmp_path, factory):
    """R29: under the offline lock no ASGI app of the MCP server is handed out."""
    pytest.importorskip("mcp")
    from cma import mcp_server

    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        getattr(mcp_server.server, factory)()
    assert no_network == []


@pytest.mark.invariants
@pytest.mark.parametrize("factory", ["streamable_http_app", "sse_app"])
def test_an_asgi_app_handed_out_with_the_lock_off_refuses_once_it_is_on(home, no_network, monkeypatch, tmp_path,
                                                                        factory):
    """T17 review 4 / R53: with the lock off the factories hand out an app, as before this
    change, so embedders keep working; the app reads the lock at start-up and before every
    request, so one saved earlier serves nothing once the lock is on (R29, T17 review 1)."""
    pytest.importorskip("mcp")
    from cma import mcp_server

    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    (home / "firm.yaml").write_text("offline_lock: false\n")
    app = getattr(mcp_server.server, factory)()
    assert isinstance(app, mcp_server.LockCheckedASGI) and callable(app)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    sent = []

    async def send(message):
        sent.append(message)

    async def http_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def lifespan_receive():
        return {"type": "lifespan.startup"}

    asyncio.run(app({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, http_receive, send))
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 503
    sent.clear()
    asyncio.run(app({"type": "lifespan"}, lifespan_receive, send))
    assert sent == [{"type": "lifespan.startup.failed", "message": sent[0]["message"]}]
    assert "offline lock" in sent[0]["message"] and no_network == []



@pytest.mark.invariants
def test_p5_a_custom_policys_free_text_never_reaches_the_prompt(home, tmp_path):
    """T17 review 1 / P5: a custom policy's rule description could carry case or customer
    text; the prompt names such a rule only by its id, metric and requirement."""
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    custom = policy.load_policy()
    custom["rules"][0] = {**custom["rules"][0], "description": "SNTLCASE paid by SNTLPERSON"}
    p = policy.evaluate(ms, custom)
    user = draft_prompt(st, ms, p)
    assert "SNTLCASE" not in user and "SNTLPERSON" not in user and "(custom rule)" in user
    shipped = policy.evaluate(ms)
    assert "(custom rule)" not in draft_prompt(st, ms, shipped)


@pytest.mark.contracts
@pytest.mark.parametrize("start", [
    lambda s: s.run("sse"),
    lambda s: s.run("streamable-http"),
    lambda s: asyncio.run(s.run_sse_async()),
    lambda s: asyncio.run(s.run_streamable_http_async()),
])
def test_http_transports_still_start_when_lock_is_off(home, monkeypatch, tmp_path, start):
    """R53, T17 review 2: refusing to hand out an ASGI app must not stop the SDK's own HTTP
    run methods, which build their app and serve it straight after the lock check."""
    pytest.importorskip("mcp")
    uvicorn = pytest.importorskip("uvicorn")
    from cma import mcp_server

    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    served = []

    async def serve(self, *a, **k):
        served.append(self.config.app)
    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    start(mcp_server.server)
    assert len(served) == 1 and callable(served[0])
    # Outside a run method an app is handed out only as a lock-checked one (T17 review 4).
    assert isinstance(mcp_server.server.sse_app(), mcp_server.LockCheckedASGI)


@pytest.mark.invariants
@pytest.mark.parametrize("field", ["id", "description"])
def test_p5_a_custom_policys_rule_id_or_description_never_reaches_the_prompt(home, tmp_path, field):
    """T17 review 2 / P5: a custom policy's rule id is free text too; only a shipped-form id
    (R and digits) reaches the prompt, and the memo's [Rn] citation still works for it."""
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    custom = policy.load_policy()
    custom["rules"][0] = {**custom["rules"][0], field: "SNTLCASE JANE DOE"}
    p = policy.evaluate(ms, custom)
    user = draft_prompt(st, ms, p)
    assert "SNTLCASE" not in user and "JANE DOE" not in user
    assert "| R2 |" in user  # the other rules keep their ids


# --- T17 review 3 ---------------------------------------------------------------------

@pytest.mark.invariants
@pytest.mark.parametrize("supplied", [{"run_offline_lock": False}, {"offline_lock": False},
                                      {"run_offline_lock": False, "offline_lock": False}])
def test_p18_graph_input_cannot_override_the_firms_lock(home, no_network, sdks, tmp_path, supplied):
    """T17 review 3 / R29, P18: a client made while firm.yaml was unlocked, then the lock
    turned on before a new run: whatever the caller puts in the graph's input, the run reads
    firm.yaml itself, refuses the request before it is sent, and nothing is drafted."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    model = llm.AnthropicLLM()
    graph = build(model)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        graph.invoke({"statement_path": str(_statement(tmp_path)), **supplied},
                     {"configurable": {"thread_id": "t"}})
    assert [c for c in sdks if c[1] == "request"] == [] and no_network == []


@pytest.mark.invariants
def test_p18_a_runs_reading_is_used_once_and_never_by_a_later_run(home, no_network, sdks, tmp_path, monkeypatch):
    """T17 review 3 / P18: the reading the CLI hands to build() is the first run's only; a
    later run on the same graph, started after the lock was turned on, reads firm.yaml."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    cfg = firm.load()
    model = llm.AnthropicLLM(firm_config=cfg)
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: llm.FakeLLM().complete(s, u))
    graph = build(model, firm_config=cfg)
    first = graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "a"}})
    assert evidence_record({**first, "review": None})["egress"]["offline_lock"] is False
    (home / "firm.yaml").write_text("offline_lock: true\n")
    sent = []
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: sent.append(u) or "")
    with pytest.raises(firm.OfflineLockError):
        graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "b"}})
    assert sent == [] and no_network == []


def _custom_id_policy(rule_id):
    from cma import policy

    custom = policy.load_policy()
    custom["rules"][0] = {**custom["rules"][0], "id": rule_id}
    return custom


@pytest.mark.invariants
@pytest.mark.parametrize("rule_id", ["R4242", "R10", "R0", "R12345", "SNTL R4242"])
def test_p5_a_custom_rule_id_in_the_shipped_form_never_reaches_the_prompt(home, tmp_path, rule_id):
    """T17 review 3 / P5: a custom id spelt like a shipped one (R and digits) is still free
    text the policy's author chose, and can be a case sentinel; only the shipped policy's
    own ids reach a prompt, first draft or redraft."""
    from cma import categorise, guard, ingest, metrics, policy
    from cma.graph import redraft_ids

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    p = policy.evaluate(ms, _custom_id_policy(rule_id))
    user = draft_prompt(st, ms, p)
    assert rule_id not in user and "(custom id)" in user
    # A draft citing the custom rule with a wrong limit: the redraft line names nothing of it.
    memo = llm.FakeLLM().complete("", user) + f"\nThe limit is 0.01x [{rule_id}].\n"
    g = guard.check(memo, ms, p, required_headings=guard.REQUIRED_HEADINGS)
    ids = redraft_ids(g, ms, p)
    assert rule_id not in ids
    redraft = draft_prompt(st, ms, p, redraft=ids)
    assert rule_id not in redraft and REDRAFT_IDS.fullmatch(redraft[len(user):])


@pytest.mark.invariants
def test_p5_cli_custom_policy_prompts_carry_no_sentinel(home, no_network, sdks, tmp_path, monkeypatch):
    """T17 review 3 / P5: through `cma run --policy` with a hosted model, the initial prompt
    and every redraft prompt carry no sentinel from the statement or the custom policy."""
    import yaml

    (home / "firm.yaml").write_text("offline_lock: false\n")
    rows, truth = synth.generate("cafe", "stretched", end=date(2026, 9, 30))
    for i, row in enumerate(rows):
        row["description"] = f"R4242 SNTLDESC{i:05d} {row['description']}"
    path, _ = synth.write(rows, truth, tmp_path)
    custom = _custom_id_policy("R4242")
    custom["rules"][1] = {**custom["rules"][1], "description": "SNTLPOLICY JANE DOE"}
    pol_path = tmp_path / "custom.yaml"
    pol_path.write_text(yaml.safe_dump(custom))
    prompts = []

    def send(self, system, user):
        prompts.append(system + user)
        return llm.FakeLLM(hallucinate=True).complete(system, user) + "\nThe limit is 0.01x [R4242].\n"

    monkeypatch.setattr(llm.AnthropicLLM, "_send", send)
    r = CliRunner().invoke(app, ["run", str(path), "--llm", "anthropic", "--policy", str(pol_path),
                                 "--yes", "--reviewer", "A", "--out", str(tmp_path / "memo.md")])
    assert prompts, r.output
    assert len(prompts) > 1  # the redraft path ran
    for p in prompts:
        for sentinel in ("R4242", "SNTLDESC", "SNTLPOLICY", "JANE DOE"):
            assert sentinel not in p
    assert no_network == []


class _BindStopped(Exception):
    pass


@pytest.mark.invariants
@pytest.mark.parametrize("transport", ["run_sse_async", "run_streamable_http_async"])
@pytest.mark.parametrize("lock", ["offline_lock: false\n", "offline_lock: true\n"])
def test_p18_http_start_binds_no_socket_under_the_lock(home, monkeypatch, tmp_path, transport, lock):
    """T17 review 3 / R29, P18: the SDK's real HTTP start, with uvicorn's own startup (no
    stand-in for serve): under the lock it is refused before any socket is bound; with the
    lock off it reaches the bind of its listening socket on loopback (stopped there)."""
    pytest.importorskip("mcp")
    pytest.importorskip("uvicorn")
    from cma import mcp_server

    (home / "firm.yaml").write_text(lock)
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    bound = []

    def bind(self, address):
        bound.append(address)
        raise _BindStopped(address)

    monkeypatch.setattr(socket.socket, "bind", bind)
    start = getattr(mcp_server.server, transport)
    if "true" in lock:
        with pytest.raises(firm.OfflineLockError, match="offline lock"):
            asyncio.run(start(host="127.0.0.1", port=0))
        assert bound == []
    else:
        # uvicorn stops on the failed bind, by exiting or by raising it
        with contextlib.suppress(_BindStopped, SystemExit, BaseExceptionGroup):
            asyncio.run(start(host="127.0.0.1", port=0))
        assert bound and bound[0][0] == "127.0.0.1"


@pytest.mark.invariants
@pytest.mark.parametrize("sdk", ["anthropic", "openai"])
def test_p18_a_hosted_client_never_takes_the_callers_word_for_the_lock(home, no_network, sdks, sdk):
    """T17 review 4 / R29: a FirmConfig the caller built with the lock off, or a bare
    offline_lock=False, let a direct caller build a client or send a request while firm.yaml
    has the lock on. Only a reading load() made can allow either."""
    (home / "firm.yaml").write_text("offline_lock: true\n")
    forged = firm.FirmConfig(root=home, offline_lock=False)
    cls = llm.AnthropicLLM if sdk == "anthropic" else llm.OpenAILLM
    with pytest.raises(firm.OfflineLockError):
        cls(firm_config=forged)
    (home / "firm.yaml").write_text("offline_lock: false\n")
    client = cls()
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        client.complete("s", "u", offline_lock=False)
    with pytest.raises(firm.OfflineLockError):
        client.complete("s", "u", reading=forged)
    assert [c for c in sdks if c[1] == "request"] == [] and no_network == []


@pytest.mark.invariants
def test_p18_the_graph_runs_untraced_under_the_lock_whatever_the_environment(home, no_network, monkeypatch, tmp_path):
    """T17 review 4 / R29: LangSmith tracing, turned on in the environment, would send the
    graph's steps, statement data among them, off the machine under the offline lock."""
    from langsmith import utils as ls_utils

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "dummy")
    monkeypatch.setattr(ls_utils, "get_env_var", lambda name, *a, **k: {"TRACING": "true", "TRACING_V2": "true",
                                                                         "API_KEY": "dummy"}.get(name, k.get("default")))
    seen = []

    class Watching(llm.FakeLLM):
        def complete(self, system, user, **k):
            seen.append(ls_utils.tracing_is_enabled())
            return super().complete(system, user)

    assert ls_utils.tracing_is_enabled()  # the environment turns tracing on
    (home / "firm.yaml").write_text("offline_lock: true\n")
    graph = build(Watching())
    graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    assert seen and not any(seen) and no_network == []


@pytest.mark.invariants
def test_p18_a_reading_authorises_only_its_own_run_and_folder(home, no_network, sdks, tmp_path, monkeypatch):
    """T17 review 5 / R29: a reading load() made was accepted for any later client or request,
    from an earlier run or for another firm folder. It now authorises one client, and requests
    only while its run is going, for the folder in use; otherwise firm.yaml is read now."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    earlier = firm.load()
    llm.AnthropicLLM(firm_config=earlier)  # authorises this one client
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        llm.AnthropicLLM(firm_config=earlier)  # not a second one, now the lock is on
    other = tmp_path / "other"
    other.mkdir()
    (other / "firm.yaml").write_text("offline_lock: false\n")
    elsewhere = firm.load(other)
    with pytest.raises(firm.OfflineLockError):
        llm.AnthropicLLM(firm_config=elsewhere)  # another folder's reading
    (home / "firm.yaml").write_text("offline_lock: false\n")
    client = llm.AnthropicLLM()
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        client.complete("s", "u", reading=earlier)  # its run is not going
    assert [c for c in sdks if c[1] == "request"] == [] and no_network == []


@pytest.mark.invariants
def test_each_run_drafts_with_its_own_reading(home, no_network, sdks, tmp_path, monkeypatch):
    """T17 review 5: readings were kept by thread, so concurrent runs on one thread could take
    each other's; each run's reading now travels in its own config."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    seen = []
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: llm.FakeLLM().complete(s, u))
    real = llm.HostedLLM.complete

    def watch(self, system, user, **k):
        seen.append(k.get("reading"))
        return real(self, system, user, **k)

    monkeypatch.setattr(llm.HostedLLM, "complete", watch)
    mine = firm.load()
    graph = build(llm.AnthropicLLM(), firm_config=mine)
    graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    assert seen and all(r is mine for r in seen) and no_network == []


@pytest.mark.invariants
def test_p18_a_resumed_review_is_untraced_once_the_lock_is_on(home, no_network, monkeypatch, tmp_path):
    """T17 review 5 / R29: a review drafted with the lock off, resumed after the firm turned it
    on, ran traced by its drafting lock; the lock now decides too."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from langsmith import utils as ls_utils

    saver = InMemorySaver()
    (home / "firm.yaml").write_text("offline_lock: false\n")
    cfg = {"configurable": {"thread_id": "t"}}
    build(llm.FakeLLM(), checkpointer=saver).invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    monkeypatch.setattr(ls_utils, "get_env_var", lambda name, *a, **k: {"TRACING": "true", "TRACING_V2": "true",
                                                                         "API_KEY": "dummy"}.get(name, k.get("default")))
    seen = []
    import cma.graph as g

    real_finalise_hook = g.appendix

    def watching(*a, **k):
        seen.append(ls_utils.tracing_is_enabled())
        return real_finalise_hook(*a, **k)

    monkeypatch.setattr(g, "appendix", watching)
    later = build(llm.FakeLLM(), checkpointer=saver)  # a new process's graph: no reading of its own yet
    later.invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    assert seen and not any(seen) and no_network == []



@pytest.mark.invariants
def test_p18_an_unchanged_firm_yaml_is_read_once_in_a_whole_cli_run(home, no_network, sdks, monkeypatch, tmp_path):
    """The draft takes one reading and the resume, a run of its own, takes one more as it
    resumes, even with firm.yaml unchanged (T17 reviews 6 and 11)."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    reads = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: reads.append(1) or real_load(*a, **k))
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: llm.FakeLLM().complete(s, u))
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic", "--yes",
                                 "--reviewer", "A", "--out", str(tmp_path / "memo.md")])
    assert r.exit_code == 0, r.output
    assert len(reads) == 2 and no_network == []


@pytest.mark.invariants
def test_p18_a_reading_taken_before_the_lock_changed_builds_no_client(home, no_network, sdks):
    """T17 review 6: an unused reading taken while the lock was off authorised a client after
    the firm locked; a reading decides a client only while firm.yaml is unchanged since it."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    before = firm.load()
    import os
    import time
    time.sleep(0.01)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    os.utime(home / "firm.yaml")
    with pytest.raises(firm.OfflineLockError):
        llm.AnthropicLLM(firm_config=before)
    assert [c for c in sdks if c[1] == "client"] == [] and no_network == []


@pytest.mark.invariants
def test_p18_one_reading_drives_one_run_however_many_graphs_hold_it(home, no_network, sdks, tmp_path, monkeypatch):
    """T17 review 6: the same unlocked reading handed to two graphs drove both runs; the second
    run reads firm.yaml now, and under the lock refuses its hosted request."""
    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: llm.FakeLLM().complete(s, u))
    shared = firm.load()
    model = llm.AnthropicLLM()
    first, second = build(model, firm_config=shared), build(model, firm_config=shared)
    first.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "a"}})
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        second.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "b"}})
    assert no_network == []


@pytest.mark.invariants
def test_p5_a_custom_rule_reusing_a_shipped_id_is_still_custom(home, tmp_path):
    """T17 review 6 / P5: a custom rule spelt with a shipped id (a sentinel can be) passed the
    id filter; a rule counts as shipped only if it is the shipped rule whole."""
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    custom = policy.load_policy()
    custom["rules"][0] = {**custom["rules"][0], "description": "SNTLCASE", "threshold": 0.5}
    user = draft_prompt(st, ms, policy.evaluate(ms, custom))
    shipped_id = custom["rules"][0]["id"]
    assert "SNTLCASE" not in user and f"| {shipped_id} |" not in user and "(custom id)" in user


@pytest.mark.invariants
def test_p18_a_saved_app_reads_the_lock_when_start_up_is_asked_for(home, no_network, monkeypatch, tmp_path):
    """T17 review 6 / R29: the saved app read the lock before the start-up event arrived, so a
    lock turned on while start-up was delayed could still start the server."""
    pytest.importorskip("mcp")
    from cma import mcp_server

    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    (home / "firm.yaml").write_text("offline_lock: false\n")
    app = mcp_server.server.sse_app()
    sent = []

    async def send(message):
        sent.append(message)

    async def delayed_startup():
        (home / "firm.yaml").write_text("offline_lock: true\n")  # turned on while start-up waits
        return {"type": "lifespan.startup"}

    asyncio.run(app({"type": "lifespan"}, delayed_startup, send))
    assert sent and sent[0]["type"] == "lifespan.startup.failed" and no_network == []



@pytest.mark.invariants
def test_p18_a_firm_yaml_replaced_with_one_of_the_same_size_and_time_is_still_a_change(home, no_network, sdks):
    """T17 review 7: a reading was judged unchanged by firm.yaml's size and time, so a file of the
    same length put back with the same time let an obsolete unlocked reading build a client."""
    import os

    (home / "firm.yaml").write_text("offline_lock: false\n")
    st = (home / "firm.yaml").stat()
    before = firm.load()
    (home / "firm.yaml").write_text("offline_lock: truee\n"[:len("offline_lock: false\n")])
    os.utime(home / "firm.yaml", ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (home / "firm.yaml").stat().st_size == st.st_size
    assert not firm.unchanged_since(before)
    with pytest.raises((firm.OfflineLockError, firm.FirmConfigError)):
        llm.AnthropicLLM(firm_config=before)
    assert [c for c in sdks if c[1] == "client"] == [] and no_network == []


@pytest.mark.invariants
def test_p5_a_custom_rule_differing_only_in_a_referral_band_is_custom(home, tmp_path):
    """T17 review 7 / P5: the whole-rule comparison ignored referral bands."""
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    custom = policy.load_policy()
    rule = custom["rules"][0]
    custom["rules"][0] = {**rule, "refer_below": float(rule.get("refer_below") or rule["threshold"]) + 7.5}
    user = draft_prompt(st, ms, policy.evaluate(ms, custom))
    assert f"| {rule['id']} |" not in user and "(custom id)" in user


@pytest.mark.invariants
def test_p18_the_app_a_native_http_run_serves_refuses_once_the_lock_is_on(home, no_network, monkeypatch, tmp_path):
    """T17 review 8 / R29: the native HTTP run methods handed Uvicorn the raw app, so a lock
    turned on after their entry check left requests outside the lock gate."""
    pytest.importorskip("mcp")
    uvicorn = pytest.importorskip("uvicorn")
    from cma import mcp_server

    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    served = []

    async def serve(self, *a, **k):
        served.append(self.config.app)
    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    asyncio.run(mcp_server.server.run_streamable_http_async())
    assert served and isinstance(served[0], mcp_server.LockCheckedASGI)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(served[0]({"type": "http", "method": "POST", "path": "/mcp", "headers": []}, receive, send))
    assert sent[0]["status"] == 503 and no_network == []


@pytest.mark.invariants
def test_p5_a_custom_rule_that_changes_only_its_failure_action_is_custom(home, tmp_path):
    """T17 review 8 / P5: the whole-rule comparison left out what a breach means (on_fail) and
    what a missing figure means (when_missing)."""
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    for field, value in (("on_fail", {"refer": "fail", "fail": "refer"}), ("when_missing", None)):
        custom = policy.load_policy()
        rule = custom["rules"][0]
        new = value[rule["on_fail"]] if value else ("fail" if rule.get("when_missing", "refer") != "fail" else "pass")
        custom["rules"][0] = {**rule, field: new}
        user = draft_prompt(st, ms, policy.evaluate(ms, custom))
        assert f"| {rule['id']} |" not in user and "(custom id)" in user, field
    shipped = draft_prompt(st, ms, policy.evaluate(ms))
    assert "(custom id)" not in shipped


@pytest.mark.invariants
def test_p18_a_suspended_stream_lends_no_run_its_tracing_decision(home, no_network, monkeypatch, tmp_path):
    """T17 review 8 / R29: a stream suspended between items marked a run as going, so a graph
    run started meanwhile, after the firm locked, took the stream's unlocked decision."""
    from langsmith import utils as ls_utils

    (home / "firm.yaml").write_text("offline_lock: false\n")
    outer = build(llm.FakeLLM()).stream({"statement_path": str(_statement(tmp_path))},
                                        {"configurable": {"thread_id": "s"}})
    next(outer)  # the stream is now suspended between items
    (home / "firm.yaml").write_text("offline_lock: true\n")
    monkeypatch.setattr(ls_utils, "get_env_var", lambda name, *a, **k: {"TRACING": "true", "TRACING_V2": "true",
                                                                         "API_KEY": "dummy"}.get(name, k.get("default")))
    seen = []

    class Watching(llm.FakeLLM):
        def complete(self, system, user, **k):
            seen.append(ls_utils.tracing_is_enabled())
            return super().complete(system, user)

    build(Watching()).invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    outer.close()
    assert seen and not any(seen) and no_network == []


@pytest.mark.invariants
@pytest.mark.parametrize("as_manager", [False, True])
def test_p18_a_tracer_handed_in_is_taken_out_under_the_lock(home, no_network, monkeypatch, tmp_path, as_manager):
    """T17 review 9 / R29: turning ambient tracing off left a LangChainTracer passed in the
    run's callbacks active, which would export the run's inputs and statement data."""
    from langchain_core.callbacks import CallbackManager
    from langchain_core.tracers.langchain import LangChainTracer

    tracer = LangChainTracer(client=types.SimpleNamespace())
    seen = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: seen.append(run), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: seen.append(run), raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    callbacks = CallbackManager(handlers=[tracer], inheritable_handlers=[tracer]) if as_manager else [tracer]
    build(llm.FakeLLM()).invoke({"statement_path": str(_statement(tmp_path))},
                                {"configurable": {"thread_id": "t"}, "callbacks": callbacks})
    assert seen == [] and no_network == []



@pytest.mark.invariants
def test_p18_a_separate_graph_run_inside_another_takes_its_own_lock(home, no_network, monkeypatch, tmp_path):
    """T17 review 10 / R29: a separate graph invoked during another graph's unlocked run took the
    outer run's decision, skipping its own lock and the removal of a tracer handed to it."""
    from langchain_core.tracers.langchain import LangChainTracer

    seen = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: seen.append(run), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: seen.append(run), raising=False)
    (home / "firm.yaml").write_text("offline_lock: false\n")
    inner_done = []

    class Outer(llm.FakeLLM):
        def complete(self, system, user, **k):
            if not inner_done:
                (home / "firm.yaml").write_text("offline_lock: true\n")
                tracer = LangChainTracer(client=types.SimpleNamespace())
                build(llm.FakeLLM()).invoke({"statement_path": str(_statement(tmp_path))},
                                            {"configurable": {"thread_id": "inner"}, "callbacks": [tracer]})
                inner_done.append(1)
            return super().complete(system, user)

    build(Outer()).invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "outer"}})
    assert inner_done and seen == [] and no_network == []


@pytest.mark.invariants
def test_p18_evidence_records_the_lock_in_force_when_the_review_was_resumed(home, no_network, tmp_path):
    """T17 review 10 / R29: a review drafted with the lock off and resumed after the firm locked
    recorded only the draft's lock; the resume's lock is recorded beside it."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    build(llm.FakeLLM(), checkpointer=saver).invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    final = build(llm.FakeLLM(), checkpointer=saver).invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    egress = evidence_record({**final, "ruleset": None})["egress"]
    assert egress["offline_lock"] is False and egress["resume_offline_lock"] is True


@pytest.mark.invariants
def test_p18_a_separate_run_on_the_same_graph_object_takes_its_own_tracer_filtering(home, no_network, monkeypatch, tmp_path):
    """T17 review 11 / R29: a run started on the same graph object during one of its own runs was
    taken for invoke's own stream, so a LangSmith tracer handed to it stayed in its callbacks."""
    from langchain_core.tracers.langchain import LangChainTracer

    import cma.graph as g

    seen = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: seen.append(run), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: seen.append(run), raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    inner_done, handed = [], []
    real_strip = g._without_network_tracers
    monkeypatch.setattr(g, "_without_network_tracers", lambda cbs: handed.append(cbs) or real_strip(cbs))

    class Outer(llm.FakeLLM):
        def complete(self, system, user, **k):
            if not inner_done:
                tracer = LangChainTracer(client=types.SimpleNamespace())
                inner_done.append(1)
                graph.invoke({"statement_path": str(_statement(tmp_path))},
                             {"configurable": {"thread_id": "inner"}, "callbacks": [tracer]})
            return super().complete(system, user)

    graph = build(Outer())
    graph.invoke({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "outer"}})
    assert inner_done and seen == [] and no_network == []
    assert any(cbs and any(isinstance(c, LangChainTracer) for c in cbs) for cbs in handed)


@pytest.mark.invariants
def test_p18_a_resume_takes_its_own_reading_with_firm_yaml_unchanged(home, no_network, monkeypatch, tmp_path):
    """T17 review 11 / R29: with firm.yaml unchanged, a resume on the same graph reused the
    draft's reading; revision 32 has a resume read firm.yaml as it resumes."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    (home / "firm.yaml").write_text("offline_lock: false\n")
    reads = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: reads.append(1) or real_load(*a, **k))
    cfg = {"configurable": {"thread_id": "t"}}
    graph = build(llm.FakeLLM(), checkpointer=InMemorySaver())
    graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    drafted = len(reads)
    graph.invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    assert drafted == 1 and len(reads) == 2 and no_network == []


def test_r29_evidence_of_a_pre_lock_draft_keeps_the_lock_in_force_at_resume():
    """T17 review 11 / R29: the evidence of a draft made before the offline lock existed dropped
    the lock its resume recorded at finalisation."""
    import cma.graph as g

    egress = g._egress({"model": "fake:echo", "resume_offline_lock": True})
    assert egress["offline_lock"] is False and egress["resume_offline_lock"] is True
    assert "resume_offline_lock" not in g._egress({"model": "fake:echo"})


@pytest.mark.invariants
def test_p18_a_pre_lock_resume_reads_firm_yaml_not_a_reading_handed_to_build(home, no_network, monkeypatch, tmp_path):
    """T17 review 12 / R29: resuming a draft from before the lock (no lock in its checkpoint) took
    the reading handed to build() instead of reading firm.yaml as it resumed."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    first = build(llm.FakeLLM(), checkpointer=saver)
    first.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    first.update_state(cfg, {"offline_lock": None, "egress": None})  # as a pre-lock draft left it
    handed = firm.load()
    reads = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: reads.append(1) or real_load(*a, **k))
    later = build(llm.FakeLLM(), checkpointer=saver, firm_config=handed)
    final = later.invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    assert reads and firm.take_for_run(handed) and no_network == []
    assert evidence_record({**final, "ruleset": None})["egress"]["resume_offline_lock"] is False


@pytest.mark.invariants
def test_p5_a_custom_rule_equal_to_a_shipped_one_is_sent_in_the_shipped_spelling(home, tmp_path):
    """T17 review 12 / P5: a custom rule equal to a shipped rule but writing its limit as another
    spelling of the same number (12.0 for 12) sent that spelling in the prompt's detail."""
    import copy

    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    shipped = policy.load_policy()
    custom = copy.deepcopy({k: v for k, v in shipped.items() if not k.startswith("_")})
    custom["name"] = "Our own"
    for r in custom["rules"]:
        for key in ("threshold", "refer_below", "refer_above"):
            if isinstance(r.get(key), int):
                r[key] = float(r[key])  # 12 written as 12.0
    mine = policy.evaluate(ms, custom)
    ours = policy.evaluate(ms, shipped)
    assert any(".0" in r.detail for r in mine.rules)  # the custom spelling is in its own detail
    assert (draft_prompt(st, ms, mine).split("Policy table:")[1]
            == draft_prompt(st, ms, ours).split("Policy table:")[1])


@pytest.mark.invariants
@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_p18_the_sdk_follows_no_redirect_and_checks_the_lock_before_every_request(home, monkeypatch, provider):
    """T17 review 13 / R29: both SDKs follow redirects by default, so one `complete` could send
    a second HTTP request past its one lock check; their HTTP client now follows none and checks
    the lock before every request it sends."""
    sdk = pytest.importorskip(provider)
    import importlib

    base = sdk._base_client
    httpx = importlib.import_module("httpx2" if "httpx2" in base.__dict__ else "httpx")
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(307, headers={"location": "https://elsewhere.example/steal"})

    real = sdk.Anthropic if provider == "anthropic" else sdk.OpenAI

    def make(**kwargs):
        return real(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)

    monkeypatch.setattr(sdk, "Anthropic" if provider == "anthropic" else "OpenAI", make)
    (home / "firm.yaml").write_text("offline_lock: false\n")
    model = llm.get_llm(provider)
    with pytest.raises(Exception):  # noqa: B017 - the SDK's own error for a 307 it does not follow
        model.complete("s", "u")
    assert len(sent) == 1  # the redirect was not followed

    # A request made through the SDK client directly, with no `complete` check before it, is
    # checked by the HTTP client itself: refused under the lock before anything is sent.
    sent.clear()
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(Exception) as e:
        model._send("s", "u")
    assert llm._lock_error_in(e.value) is not None and sent == []


@pytest.mark.invariants
def test_p18_a_request_complete_allowed_is_decided_by_the_runs_reading_not_a_second_one(home, monkeypatch):
    """T17 review 14 / R29: the HTTP client read firm.yaml again for the request `complete` had
    allowed on the run's reading, so a lock turned on mid-run decided a run already going."""
    sdk = pytest.importorskip("anthropic")
    import importlib

    httpx = importlib.import_module("httpx2" if "httpx2" in sdk._base_client.__dict__ else "httpx")
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "x",
                                         "stop_reason": "end_turn", "stop_sequence": None,
                                         "usage": {"input_tokens": 1, "output_tokens": 1},
                                         "content": [{"type": "text", "text": "ok"}]})

    real = sdk.Anthropic
    monkeypatch.setattr(sdk, "Anthropic", lambda **kw: real(api_key="test", http_client=httpx.Client(
        transport=httpx.MockTransport(handler)), **kw))
    (home / "firm.yaml").write_text("offline_lock: false\n")
    model = llm.get_llm("anthropic")
    reads = []
    real_load = firm.load
    real_check = model._check

    def check_then_lock(*a):
        real_check(*a)
        (home / "firm.yaml").write_text("offline_lock: true\n")  # turned on mid-run
        monkeypatch.setattr(firm, "load", lambda *x, **y: reads.append(1) or real_load(*x, **y))

    monkeypatch.setattr(model, "_check", check_then_lock)
    assert model.complete("s", "u") == "ok" and len(sent) == 1 and reads == []


@pytest.mark.invariants
def test_p18_a_tracer_inherited_from_the_calling_runnable_is_taken_out_under_the_lock(home, no_network, monkeypatch, tmp_path):
    """T17 review 13 / R29: a graph called from inside another runnable inherits its callbacks
    through the runnable context; only explicitly passed callbacks were filtered, so an inherited
    LangSmith tracer stayed installed under the lock."""
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tracers.langchain import LangChainTracer

    names = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: names.append((run.name, id(self))), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: names.append((run.name, id(self))), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: None, raising=False)
    monkeypatch.setattr(LangChainTracer, "_update_run_single", lambda self, run: None, raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    graph = build(llm.FakeLLM())

    def calling_runnable(x):
        return graph.invoke(x, {"configurable": {"thread_id": "inner"}})

    tracer = LangChainTracer(client=types.SimpleNamespace())
    RunnableLambda(calling_runnable).invoke({"statement_path": str(_statement(tmp_path))}, {"callbacks": [tracer]})
    assert {n for n, _ in names} <= {"calling_runnable"} and no_network == []


@pytest.mark.invariants
def test_p18_a_resume_that_drafts_is_checked_against_the_lock_it_read_at_resume(home, no_network, monkeypatch, tmp_path):
    """T17 review 13 / R29: a review resumed before its draft (a checkpoint paused there) sent its
    hosted request under the draft's lock, the client reading firm.yaml again; the lock its
    reading found at resume decides, as its evidence records."""
    from langgraph.checkpoint.memory import InMemorySaver

    import cma.graph as g

    sent = []

    class Hosted(llm.HostedLLM):
        name = "test"

        def _send(self, system, user):
            sent.append(1)
            return llm.FakeLLM().complete(system, user)

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    graph = build(Hosted(), checkpointer=saver)
    graph.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    graph.update_state(cfg, {}, as_node="policy")  # paused again before its draft
    sent.clear()
    (home / "firm.yaml").write_text("offline_lock: true\n")  # the lock the resume reads
    real_prompt = g.draft_prompt

    def turned_off_again(*a, **k):
        (home / "firm.yaml").write_text("offline_lock: false\n")
        return real_prompt(*a, **k)

    monkeypatch.setattr(g, "draft_prompt", turned_off_again)
    with pytest.raises(firm.OfflineLockError):
        build(Hosted(), checkpointer=saver).invoke(None, cfg)
    assert sent == [] and no_network == []


@pytest.mark.invariants
def test_p18_an_async_resume_keeps_the_lock_its_draft_was_made_under(home, no_network, tmp_path):
    """T17 review 14 / R29: an async resume looked the stored lock up synchronously, which
    AsyncSqliteSaver refuses on its own event loop; the failure was read as a draft from before
    the lock, so a draft made under the lock was resumed, and drafted, unlocked."""
    import asyncio

    pytest.importorskip("aiosqlite")
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command

    sent = []

    class Hosted(llm.HostedLLM):
        name = "test"

        def _send(self, system, user):
            sent.append(1)
            return llm.FakeLLM().complete(system, user)

    cfg = {"configurable": {"thread_id": "t"}}

    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "c.sqlite")) as saver:
            (home / "firm.yaml").write_text("offline_lock: true\n")
            first = build(llm.FakeLLM(), checkpointer=saver)
            await first.ainvoke({"statement_path": str(_statement(tmp_path))}, cfg)
            await first.aupdate_state(cfg, {}, as_node="policy")  # paused again before its draft
            (home / "firm.yaml").write_text("offline_lock: false\n")
            with pytest.raises(firm.OfflineLockError):
                await build(Hosted(), checkpointer=saver).ainvoke(None, cfg)
            # Drafted again offline, then decided: the resume's lock is the draft's.
            await build(llm.FakeLLM(), checkpointer=saver).ainvoke(None, cfg)
            return await build(llm.FakeLLM(), checkpointer=saver).ainvoke(
                Command(resume={"approved": True, "reviewer": "A"}), cfg)

    final = asyncio.run(scenario())
    assert sent == [] and no_network == []
    assert evidence_record({**final, "ruleset": None})["egress"]["resume_offline_lock"] is True


def test_r30_a_shipped_rule_is_still_known_after_a_checkpoint_round_trip(home, tmp_path):
    """T17 review 14 / R30: a checkpoint dropped each rule's fail and missing-figure actions, so a
    resumed draft masked the shipped rules as custom; they are kept in a checkpoint, and left out
    of evidence and MCP output as before."""
    import cma.graph as g
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    result = policy.evaluate(ms)
    serde = g._serde()
    restored = serde.loads_typed(serde.dumps_typed(result))
    assert restored == result and restored.rules[0].on_fail
    assert g.rules_table(restored, for_prompt=True) == g.rules_table(result, for_prompt=True)
    assert "(custom id)" not in g.rules_table(restored, for_prompt=True)
    assert not any({"on_fail", "when_missing"} & set(r) for r in result.evidence_dump()["rules"])


@pytest.mark.invariants
def test_p18_a_reading_is_parsed_from_the_bytes_its_stamp_hashes(home, sdks, monkeypatch):
    """T17 review 15 / R29: the stamp hashed one read of firm.yaml and the settings came from a
    second; a file changed between them paired an unlocked reading with the locked file's stamp,
    which then built a hosted client once the locked file was back."""
    import pathlib

    (home / "firm.yaml").write_text("offline_lock: true\n")
    real_read_text = pathlib.Path.read_text
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, *a, **k: "offline_lock: false\n"
                        if self.name == "firm.yaml" else real_read_text(self, *a, **k))  # a second read sees a change
    reading = firm.load()
    assert reading.offline_lock is not False
    with pytest.raises(firm.OfflineLockError):
        llm.get_llm("anthropic", firm_config=reading)
    assert ("anthropic", "client") not in sdks


@pytest.mark.invariants
def test_r29_a_resume_that_drafts_records_its_own_model_and_destination(home, no_network, tmp_path):
    """T17 review 15 / R29: a resume that drafted on a hosted model left the first draft's model
    and destination in the evidence."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    class Hosted(llm.HostedLLM):
        name = "test"
        model = "m1"

        def _send(self, system, user):
            return llm.FakeLLM().complete(system, user)

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    first = build(llm.FakeLLM(), checkpointer=saver)
    first.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    first.update_state(cfg, {}, as_node="policy")  # paused again before its draft
    later = build(Hosted(), checkpointer=saver)
    later.invoke(None, cfg)
    final = later.invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    record = evidence_record({**final, "ruleset": None})
    assert record["model"] == "test:m1" and record["egress"]["destination"] == "test:m1" and no_network == []


def test_p5_a_shipped_rule_from_a_checkpoint_without_its_actions_is_still_known(home, tmp_path):
    """T17 review 15 / R30: a checkpoint from before rule actions were kept restores a shipped
    rule with neither, which was masked as custom, so the memo could not cite it."""
    import cma.graph as g
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    result = policy.evaluate(metrics.compute(st))
    legacy = result.model_copy(update={"rules": [r.model_copy(update={"on_fail": "", "when_missing": ""})
                                                 for r in result.rules]})
    assert g.rules_table(legacy, for_prompt=True) == g.rules_table(result, for_prompt=True)
    changed = legacy.model_copy(update={"rules": [legacy.rules[0].model_copy(update={"threshold": 99.0}),
                                                  *legacy.rules[1:]]})
    assert "(custom id)" in g.rules_table(changed, for_prompt=True)


@pytest.mark.invariants
def test_p18_a_suspended_streams_reading_allows_no_other_callers_request(home, no_network, monkeypatch, tmp_path):
    """T17 review 16 / R29: a reading allowed a hosted request whenever its run was going, so
    another caller holding a suspended stream's unlocked reading sent one after the firm locked."""
    import threading

    readings = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: readings.append(real_load(*a, **k)) or readings[-1])
    sent = []

    class Hosted(llm.HostedLLM):
        name = "test"

        def _send(self, system, user):
            sent.append(1)
            return "ok"

    (home / "firm.yaml").write_text("offline_lock: false\n")
    stream = build(llm.FakeLLM()).stream({"statement_path": str(_statement(tmp_path))},
                                         {"configurable": {"thread_id": "s"}})
    next(stream)  # the run is going, suspended between items
    reading = readings[0]
    assert reading.offline_lock is False and not firm.allows_request(reading)  # without the run's key
    (home / "firm.yaml").write_text("offline_lock: true\n")
    outcome = []

    def other_caller():
        try:
            Hosted().complete("s", "u", reading=reading)
            outcome.append("sent")
        except firm.OfflineLockError:
            outcome.append("refused")

    worker = threading.Thread(target=other_caller)
    worker.start()
    worker.join()
    stream.close()
    assert outcome == ["refused"] and sent == [] and no_network == []


@pytest.mark.invariants
def test_r29_a_resume_that_drafts_and_pauses_records_its_lock_with_its_destination(home, no_network, tmp_path):
    """T17 review 16 / R29: a resume that drafted wrote its model and destination, but its lock
    only at finalisation, so a review paused again had evidence without the resume's lock."""
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    first = build(llm.FakeLLM(), checkpointer=saver)
    first.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    first.update_state(cfg, {}, as_node="policy")  # paused again before its draft
    (home / "firm.yaml").write_text("offline_lock: true\n")
    later = build(llm.FakeLLM(), checkpointer=saver)
    later.invoke(None, cfg)  # drafts, then pauses for review again
    values = later.get_state(cfg).values
    assert values["review"] is None and values["draft_resume_offline_lock"] is True
    assert evidence_record({**values, "ruleset": None})["egress"]["draft_resume_offline_lock"] is True


def test_p5_a_rule_without_its_actions_from_another_policy_is_masked(home, tmp_path):
    """T17 review 16 / R30, P5: a rule restored without its actions was taken for the shipped rule
    whatever policy it came from; only a result from the shipped policy file, by its hash, can
    say its actions are the shipped rule's."""
    import cma.graph as g
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    result = policy.evaluate(metrics.compute(st))
    legacy = result.model_copy(update={"rules": [r.model_copy(update={"on_fail": "", "when_missing": ""})
                                                 for r in result.rules]})
    other = legacy.model_copy(update={"policy_sha256": "0" * 64})
    assert "(custom id)" not in g.rules_table(legacy, for_prompt=True)
    table = g.rules_table(other, for_prompt=True)
    assert all(f"| {r.rule_id} |" not in table for r in other.rules) and "(custom id)" in table


@pytest.mark.invariants
def test_p18_a_suspended_streams_reading_allows_no_request_on_its_own_thread(home, no_network, monkeypatch, tmp_path):
    """T17 review 17 / R29: run ownership was set in the context of the code consuming a sync
    stream, so while the stream was suspended, other code on that thread holding its unlocked
    reading sent a hosted request after the firm locked. A request needs the run's own key,
    which only the run's config holds."""
    readings = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: readings.append(real_load(*a, **k)) or readings[-1])
    sent = []

    class Hosted(llm.HostedLLM):
        name = "test"

        def _send(self, system, user):
            sent.append(1)
            return "ok"

    (home / "firm.yaml").write_text("offline_lock: false\n")
    stream = build(llm.FakeLLM()).stream({"statement_path": str(_statement(tmp_path))},
                                         {"configurable": {"thread_id": "s"}})
    next(stream)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    with pytest.raises(firm.OfflineLockError):
        Hosted().complete("s", "u", reading=readings[0])  # same thread, stream suspended
    stream.close()
    assert sent == [] and no_network == []


@pytest.mark.invariants
def test_r29_a_finalising_resume_keeps_the_lock_a_drafting_resume_was_checked_against(home, no_network, tmp_path):
    """T17 review 17 / R29: finalise wrote its own resume's lock over the one a drafting resume
    recorded, so the evidence named the drafting resume's destination beside another lock."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    first = build(llm.FakeLLM(), checkpointer=saver)
    first.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    first.update_state(cfg, {}, as_node="policy")  # paused again before its draft
    build(llm.FakeLLM(), checkpointer=saver).invoke(None, cfg)  # a resume drafts, lock off, and pauses
    (home / "firm.yaml").write_text("offline_lock: true\n")
    final = build(llm.FakeLLM(), checkpointer=saver).invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    egress = evidence_record({**final, "ruleset": None})["egress"]
    assert egress["draft_resume_offline_lock"] is False and egress["resume_offline_lock"] is True


@pytest.mark.invariants
def test_p18_a_graph_copied_by_with_config_keeps_the_locks_rules(home, no_network, monkeypatch, tmp_path):
    """T17 review 18 / R29: the lock's rules for a run were attributes of the compiled graph
    object; with_config() and copy() build a new object without them, so a run through
    graph.with_config(...) under the lock kept a LangSmith tracer and, as a resume, took no
    reading and recorded no resume lock."""
    from langchain_core.tracers.langchain import LangChainTracer
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    names = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: None, raising=False)
    monkeypatch.setattr(LangChainTracer, "_update_run_single", lambda self, run: None, raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    saver = InMemorySaver()
    graph = build(llm.FakeLLM(), checkpointer=saver)
    copied = graph.with_config(tags=["copied"])
    assert type(copied) is type(graph)
    tracer = LangChainTracer(client=types.SimpleNamespace())
    cfg = {"configurable": {"thread_id": "t"}}
    copied.invoke({"statement_path": str(_statement(tmp_path))}, {**cfg, "callbacks": [tracer]})
    assert names == []
    reads = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: reads.append(1) or real_load(*a, **k))
    final = graph.copy().invoke(Command(resume={"approved": True, "reviewer": "A"}), {**cfg, "callbacks": [tracer]})
    assert reads and names == [] and no_network == []
    assert evidence_record({**final, "ruleset": None})["egress"]["resume_offline_lock"] is True


@pytest.mark.invariants
@pytest.mark.parametrize("how", ["invoke", "stream"])
def test_p18_a_callers_tracing_v2_context_does_not_trace_a_run_under_the_lock(home, no_network, monkeypatch, tmp_path, how):
    """T17 review 19 / R29: inside a caller's tracing_v2_enabled() context, LangChain added that
    context's tracer to the run's callbacks after the run's own were filtered, so a run under
    the lock was traced whatever LangSmith's own switch said."""
    from langchain_core.tracers.context import tracing_v2_enabled
    from langchain_core.tracers.langchain import LangChainTracer

    names = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: None, raising=False)
    monkeypatch.setattr(LangChainTracer, "_update_run_single", lambda self, run: None, raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    graph = build(llm.FakeLLM())
    args = ({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    with tracing_v2_enabled(client=types.SimpleNamespace()):
        if how == "invoke":
            graph.invoke(*args)
        else:
            list(graph.stream(*args))
    assert names == [] and no_network == []


@pytest.mark.invariants
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_p18_tracing_turned_on_between_stream_steps_does_not_trace_a_run_under_the_lock(
        home, no_network, monkeypatch, tmp_path, mode):
    """T17 review 20 / R29: a stream under the lock was untraced only at its first step; a caller
    entering tracing_v2_enabled() or LangSmith's tracing_context(enabled=True) between steps had
    the later steps traced."""
    import asyncio

    import langsmith
    from langchain_core.tracers.context import tracing_v2_callback_var, tracing_v2_enabled
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import utils as ls_utils

    import cma.graph as g

    seen = []  # what a later step, the draft, sees: a tracing_v2 tracer, LangSmith's switch
    real_prompt = g.draft_prompt
    monkeypatch.setattr(g, "draft_prompt", lambda *a, **k: seen.append(
        (tracing_v2_callback_var.get() is not None, ls_utils.tracing_is_enabled())) or real_prompt(*a, **k))
    names = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: None, raising=False)
    monkeypatch.setattr(LangChainTracer, "_update_run_single", lambda self, run: None, raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    graph = build(llm.FakeLLM())
    args = ({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})

    if mode == "sync":
        stream = graph.stream(*args)
        next(stream)
        with tracing_v2_enabled(client=types.SimpleNamespace()), langsmith.tracing_context(enabled=True):
            rest = list(stream)
    else:
        async def scenario():
            stream = graph.astream(*args)
            await stream.__anext__()
            with tracing_v2_enabled(client=types.SimpleNamespace()), langsmith.tracing_context(enabled=True):
                return [item async for item in stream]
        rest = asyncio.run(scenario())
    assert rest and seen == [(False, False)] and names == [] and no_network == []


@pytest.mark.invariants
@pytest.mark.parametrize("how", ["invoke", "stream"])
def test_p18_a_tracer_bound_with_with_config_is_taken_out_under_the_lock(home, no_network, monkeypatch, tmp_path, how):
    """T17 review 21 / R29: only the call's own callbacks were filtered; LangGraph merges the
    graph's bound callbacks (with_config(callbacks=[tracer])) into the run's afterwards, so a
    bound LangSmith tracer traced every step under the lock."""
    from langchain_core.tracers.langchain import LangChainTracer

    names = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: None, raising=False)
    monkeypatch.setattr(LangChainTracer, "_update_run_single", lambda self, run: None, raising=False)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    bound = build(llm.FakeLLM()).with_config(callbacks=[LangChainTracer(client=types.SimpleNamespace())])
    args = ({"statement_path": str(_statement(tmp_path))}, {"configurable": {"thread_id": "t"}})
    out = bound.invoke(*args) if how == "invoke" else list(bound.stream(*args))
    assert out and names == [] and no_network == []
    assert bound.config["callbacks"]  # the caller's graph keeps its own callbacks


@pytest.mark.invariants
def test_p5_a_custom_rules_limits_never_reach_the_prompt(home, tmp_path):
    """T17 review 10 / P5: a custom rule's threshold, a number that can come from the case, reached
    the prompt in its requirement and detail cells."""
    from cma import categorise, ingest, metrics, policy

    st = categorise.categorise(ingest.load_csv(_statement(tmp_path), account_name="ACME LTD"))
    ms = metrics.compute(st)
    custom = policy.load_policy()
    custom["rules"][0] = {**custom["rules"][0], "threshold": 424242.0}
    user = draft_prompt(st, ms, policy.evaluate(ms, custom))
    assert "424242" not in user and "424,242" not in user and "(custom requirement)" in user


@pytest.mark.invariants
def test_r29_a_resume_after_a_draft_made_under_the_lock_stays_under_it(home, no_network, monkeypatch, tmp_path):
    """Antigravity re-review of T17 / R29 revision 32: a run started unlocked, paused before its
    draft, was drafted by a resume under the lock; once firm.yaml was unlocked again, the
    finalising resume was treated as unlocked, keeping a LangSmith tracer. A resume is under the
    lock if the lock was on when its draft was made."""
    from langchain_core.tracers.langchain import LangChainTracer
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    names = []
    monkeypatch.setattr(LangChainTracer, "_start_trace", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: names.append(run.name), raising=False)
    monkeypatch.setattr(LangChainTracer, "_persist_run_single", lambda self, run: None, raising=False)
    monkeypatch.setattr(LangChainTracer, "_update_run_single", lambda self, run: None, raising=False)
    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    first = build(llm.FakeLLM(), checkpointer=saver)
    first.invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    first.update_state(cfg, {}, as_node="policy")  # paused again before its draft
    (home / "firm.yaml").write_text("offline_lock: true\n")
    build(llm.FakeLLM(), checkpointer=saver).invoke(None, cfg)  # drafted by a resume under the lock
    (home / "firm.yaml").write_text("offline_lock: false\n")
    tracer = LangChainTracer(client=types.SimpleNamespace())
    final = build(llm.FakeLLM(), checkpointer=saver).invoke(
        Command(resume={"approved": True, "reviewer": "A"}), {**cfg, "callbacks": [tracer]})
    assert names == [] and no_network == []
    egress = evidence_record({**final, "ruleset": None})["egress"]
    assert egress["draft_resume_offline_lock"] is True and egress["resume_offline_lock"] is True


@pytest.mark.invariants
def test_r29_a_new_run_on_a_finished_thread_does_not_keep_the_last_resumes_lock(home, no_network, tmp_path):
    """Antigravity re-review of T17: a fresh run on a thread whose last run had been resumed kept
    that resume's lock in its state, so its evidence recorded a resume it never had."""
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "t"}}
    (home / "firm.yaml").write_text("offline_lock: false\n")
    build(llm.FakeLLM(), checkpointer=saver).invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    build(llm.FakeLLM(), checkpointer=saver).invoke(Command(resume={"approved": True, "reviewer": "A"}), cfg)
    (home / "firm.yaml").write_text("offline_lock: true\n")
    again = build(llm.FakeLLM(), checkpointer=saver).invoke({"statement_path": str(_statement(tmp_path))}, cfg)
    egress = evidence_record({**again, "ruleset": None})["egress"]
    assert "resume_offline_lock" not in egress and egress["offline_lock"] is True
