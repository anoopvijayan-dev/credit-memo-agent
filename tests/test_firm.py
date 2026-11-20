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
def test_p18_cli_run_reads_firm_yaml_once_and_records_what_dispatch_used(home, no_network, sdks, monkeypatch,
                                                                         tmp_path):
    # The run reads firm.yaml once, at its start, and every hosted request in it is checked
    # against that reading: a lock turned on later in the run does not change the run, and
    # the evidence records the value the request was checked against (verify-09, R29).
    (home / "firm.yaml").write_text("offline_lock: false\n")
    reads = []
    real_load = firm.load
    monkeypatch.setattr(firm, "load", lambda *a, **k: reads.append(1) or real_load(*a, **k))
    real_init = llm.AnthropicLLM.__init__

    def init_then_lock(self, *a, **k):
        real_init(self, *a, **k)
        (home / "firm.yaml").write_text("offline_lock: true\n")

    monkeypatch.setattr(llm.AnthropicLLM, "__init__", init_then_lock)
    monkeypatch.setattr(llm.AnthropicLLM, "_send", lambda self, s, u: llm.FakeLLM().complete(s, u))
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic", "--yes",
                                 "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert len(reads) == 1
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"]["offline_lock"] is False
    assert record["egress"]["destination"].startswith("anthropic:")
    assert no_network == []


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
    assert record["egress"] == {"offline_lock": True, "destination": "offline", "recorded": AT_RUN_START}


def test_evidence_records_lock_off_with_offline_model(home, tmp_path):
    (home / "firm.yaml").write_text("offline_lock: false\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": False, "destination": "offline", "recorded": AT_RUN_START}


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
@pytest.mark.parametrize("lock", ["offline_lock: false\n", "offline_lock: true\n"])
def test_p18_no_asgi_app_is_handed_out_whatever_the_lock(home, no_network, monkeypatch, tmp_path, factory, lock):
    """T17 review 1 / R29: an ASGI server could open its socket for a saved app before any
    check inside it runs (lifespan off), so the server hands out no ASGI app at all."""
    pytest.importorskip("mcp")
    from cma import mcp_server

    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    (home / "firm.yaml").write_text(lock)
    with pytest.raises(mcp_server.NoASGIApp, match="ASGI"):
        getattr(mcp_server.server, factory)()
    assert issubclass(mcp_server.NoASGIApp, firm.OfflineLockError) and no_network == []



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
    # The app built for that run is not handed out afterwards either.
    with pytest.raises(mcp_server.NoASGIApp):
        mcp_server.server.sse_app()


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
