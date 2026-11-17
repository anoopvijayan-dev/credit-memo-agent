"""Firm configuration and the offline lock (R29, R30, R34; properties P18 and P5)."""

import asyncio
import json
import socket
import sys
import types
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import firm, llm, synth
from cma.cli import app
from cma.graph import REDRAFT_NOTE, build, draft_prompt, evidence_record


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
])
def test_malformed_file_is_refused(home, text, problem):
    # A malformed lock must never fall back silently to some other setting.
    (home / "firm.yaml").write_text(text)
    with pytest.raises(firm.FirmConfigError, match=r"firm\.yaml") as e:
        firm.load()
    assert problem in str(e.value)


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
def test_hosted_model_refused_under_lock(home, no_network, sdks, monkeypatch, firm_yaml, provider):
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
def test_existing_client_refused_once_the_lock_is_on(home, no_network, sdks, tmp_path, cls):
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


def test_malformed_firm_file_refuses_hosted_model(home, no_network, sdks):
    (home / "firm.yaml").write_text("offline_lock: off-ish\n")
    with pytest.raises(firm.FirmConfigError):
        llm.get_llm("anthropic")
    assert sdks == [] and no_network == []


@pytest.mark.parametrize("firm_yaml", LOCK_ON)
def test_mcp_server_refuses_to_start_under_lock(home, no_network, monkeypatch, tmp_path, firm_yaml):
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
def test_exported_mcp_server_refuses_to_start_under_lock(home, no_network, mcp_parent, monkeypatch, tmp_path,
                                                         firm_yaml, start):
    # An embedding script can import the server object and start it without main().
    from cma import mcp_server

    if firm_yaml is not None:
        (home / "firm.yaml").write_text(firm_yaml)
    monkeypatch.setenv("CMA_DATA_DIR", str(tmp_path))
    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        start(mcp_server.server)
    assert mcp_parent == [] and no_network == []


@pytest.mark.parametrize("start", START_PATHS)
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
def test_mcp_tools_refuse_under_lock(home, no_network, monkeypatch, tmp_path, tool, args):
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


def test_cli_refuses_hosted_model_under_lock(home, no_network, tmp_path):
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic"])
    assert r.exit_code == 2, r.output
    assert "offline lock" in r.output
    assert no_network == []


def test_cli_refuses_when_lock_turns_on_mid_run(home, no_network, sdks, monkeypatch, tmp_path):
    # The client is made with the lock off; the firm turns it on before the draft request.
    (home / "firm.yaml").write_text("offline_lock: false\n")
    real_init = llm.AnthropicLLM.__init__

    def init_then_lock(self, *a, **k):
        real_init(self, *a, **k)
        (home / "firm.yaml").write_text("offline_lock: true\n")

    monkeypatch.setattr(llm.AnthropicLLM, "__init__", init_then_lock)
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--llm", "anthropic",
                                 "--out", str(tmp_path / "memo.md")])
    assert r.exit_code == 2, r.output
    assert "Refused." in r.output and "offline lock" in r.output
    assert [c for c in sdks if c[1] == "request"] == [] and no_network == []
    assert not (tmp_path / "memo.md").exists()


def test_evidence_records_offline_mode(home, tmp_path):
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": True, "destination": "offline", "recorded": "at draft"}


def test_evidence_records_lock_off_with_offline_model(home, tmp_path):
    (home / "firm.yaml").write_text("offline_lock: false\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": False, "destination": "offline", "recorded": "at draft"}


@pytest.mark.parametrize("model, destination", [("fake:offline-template", "offline"),
                                                ("anthropic:claude-sonnet-5-5", "anthropic:claude-sonnet-5-5")])
def test_evidence_records_egress_for_a_draft_made_before_the_lock(home, tmp_path, model, destination):
    # A pending review drafted by the version before the lock has no lock fields in its
    # checkpoint; the record written on resume still says where the draft's data went.
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
def test_no_prompt_carries_line_level_text(home, monkeypatch, archetype, profile, hallucinate):
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
        # A redraft prompt is the first prompt plus one fixed sentence: no model output, no diagnostics.
        assert user == (first_user if i == 0 else first_user + REDRAFT_NOTE)
    _assert_only_tables_period_placeholder(first_user)


def _assert_only_tables_period_placeholder(user):
    assert "Account: Applicant\n" in user
    assert "Metrics table:" in user and "Policy table:" in user
    for line in user.splitlines():
        assert (not line or line.startswith(("Account: Applicant", "Period: ", "Metrics table:", "Policy table:",
                                               "Policy: ", "Overall policy outcome: ", "|")))


def test_prompt_uses_placeholder_not_the_account_name(home, tmp_path):
    from cma import categorise, ingest, metrics, policy

    st = ingest.load_csv(_statement(tmp_path), account_name="SNTLNAME HOLDINGS LTD")
    st = categorise.categorise(st)
    ms = metrics.compute(st)
    for redraft in (False, True):
        user = draft_prompt(st, ms, policy.evaluate(ms), redraft=redraft)
        assert "SNTLNAME" not in user
        assert f"Period: {st.start} to {st.end}\n" in user
        _assert_only_tables_period_placeholder(user.replace(REDRAFT_NOTE, "") if redraft else user)
    assert not any(ch.isdigit() for ch in REDRAFT_NOTE)
