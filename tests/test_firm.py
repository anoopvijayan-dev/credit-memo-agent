"""Firm configuration and the offline lock (R29, R30, R34; properties P18 and P5)."""

import json
import socket
from datetime import date

import pytest
from typer.testing import CliRunner

from cma import firm, llm, synth
from cma.cli import app
from cma.graph import build, draft_prompt


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


@pytest.mark.parametrize("firm_yaml", LOCK_ON)
@pytest.mark.parametrize("provider", ["anthropic", "openai", "Anthropic", "OPENAI"])
def test_hosted_model_refused_under_lock(home, no_network, monkeypatch, firm_yaml, provider):
    if firm_yaml is not None:
        (home / "firm.yaml").write_text(firm_yaml)
    created = []
    anthropic = pytest.importorskip("anthropic")
    openai = pytest.importorskip("openai")
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: created.append("anthropic"))
    monkeypatch.setattr(openai, "OpenAI", lambda *a, **k: created.append("openai"))

    with pytest.raises(firm.OfflineLockError, match="offline lock"):
        llm.get_llm(provider)
    monkeypatch.setenv("CMA_LLM", provider)
    with pytest.raises(firm.OfflineLockError):
        llm.get_llm()
    for cls in (llm.AnthropicLLM, llm.OpenAILLM):
        with pytest.raises(firm.OfflineLockError):
            cls()
    assert created == [] and no_network == []


def test_offline_model_is_allowed_under_lock(home, no_network):
    assert llm.get_llm("fake").name == "fake"
    assert no_network == []


def test_hosted_model_allowed_when_lock_is_off(home, no_network, monkeypatch):
    pytest.importorskip("anthropic")
    (home / "firm.yaml").write_text("offline_lock: false\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert llm.get_llm("anthropic").name == "anthropic"  # creating the client opens no connection
    assert no_network == []


def test_malformed_firm_file_refuses_hosted_model(home, no_network):
    (home / "firm.yaml").write_text("offline_lock: off-ish\n")
    with pytest.raises(firm.FirmConfigError):
        llm.get_llm("anthropic")
    assert no_network == []


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


def test_evidence_records_offline_mode(home, tmp_path):
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": True, "destination": "offline"}


def test_evidence_records_lock_off_with_offline_model(home, tmp_path):
    (home / "firm.yaml").write_text("offline_lock: false\n")
    out = tmp_path / "memo.md"
    r = CliRunner().invoke(app, ["run", str(_statement(tmp_path)), "--yes", "--reviewer", "A", "--out", str(out)])
    assert r.exit_code == 0, r.output
    record = json.loads((tmp_path / "memo.evidence.json").read_text())
    assert record["egress"] == {"offline_lock": False, "destination": "offline"}


# --- P5: a prompt carries only the tables, the period and the placeholder name ----

SENTINELS = ["SNTLDESC", "SNTLNAME", "SNTLPATH"]


class RecordingLLM(llm.FakeLLM):
    """The offline model, keeping every prompt it is given."""

    def __init__(self, hallucinate=False):
        super().__init__(hallucinate=hallucinate)
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(system + "\n" + user)
        return super().complete(system, user)


def _sentinel_statement(tmp_path, profile, archetype):
    """A real statement in which every description carries a sentinel, under a path that does too."""
    rows, truth = synth.generate(archetype, profile, end=date(2026, 9, 30))
    for i, row in enumerate(rows):
        # Prefix and suffix, so rules that read either end still see payer text around the sentinel.
        row["description"] = f"{row['description']} SNTLDESC{i:05d}" if i % 2 else f"SNTLDESC{i:05d} {row['description']}"
    csv_path, _ = synth.write(rows, truth, tmp_path / "SNTLPATH")
    return csv_path


@pytest.mark.parametrize("archetype", list(synth.ARCHETYPES))
@pytest.mark.parametrize("profile", list(synth.PROFILES))
@pytest.mark.parametrize("hallucinate", [False, True])
def test_no_prompt_carries_line_level_text(home, tmp_path, archetype, profile, hallucinate):
    model = RecordingLLM(hallucinate=hallucinate)
    graph = build(model)
    cfg = {"configurable": {"thread_id": f"{archetype}-{profile}-{hallucinate}"}}
    graph.invoke({"statement_path": str(_sentinel_statement(tmp_path, profile, archetype))}, cfg)
    assert model.prompts  # a redraft adds prompts that carry the guard's feedback
    if hallucinate:
        assert len(model.prompts) > 1
    for prompt in model.prompts:
        for s in SENTINELS:
            assert s not in prompt
        assert "Account: Applicant\n" in prompt


def test_prompt_uses_placeholder_not_the_account_name(home, tmp_path):
    from cma import categorise, ingest, metrics, policy

    st = ingest.load_csv(_statement(tmp_path), account_name="SNTLNAME HOLDINGS LTD")
    st = categorise.categorise(st)
    ms = metrics.compute(st)
    user = draft_prompt(st, ms, policy.evaluate(ms), feedback="")
    assert "SNTLNAME" not in user
    assert "Account: Applicant\n" in user
    assert f"Period: {st.start} to {st.end}\n" in user
    assert "Metrics table:" in user and "Policy table:" in user
    # Nothing but the tables, the period, the placeholder and the outcome line.
    for line in user.splitlines():
        assert (not line or line.startswith(("Account: ", "Period: ", "Metrics table:", "Policy table:",
                                               "Policy: ", "Overall policy outcome: ", "|")))
