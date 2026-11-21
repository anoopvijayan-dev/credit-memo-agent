"""T19: the workspace's case summary (C5; R3, R34, P8).

The workspace loads a case's statements, computes every figure through the figures layer
(with the profile and the whole override log), and shows the money-in breakdown, the figures
and the monthly summary, money to the penny. When the money-in buckets do not reconcile with
total money in, or a credit is in no bucket or in more than one, it shows no figures at all,
and reports the difference in pence and the lines involved (R3). Every piece of text from the
case is escaped in the HTML (R34, P8).

Each test talks to a real server running in a thread, through its API (/api/case) and its page
(/), with the per-launch token."""

import csv
import http.client
import io
import json
import random
from decimal import Decimal
from html import escape
from html.parser import HTMLParser

import pytest

from cma import metrics, overrides, store
from cma.models import Category
from cma.review import server as rs
from cma.review import summary

SMALL = ("id,date,description,amount,balance\n"
         "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
         "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.25\n"
         "L3,2026-01-07,DD COUNCIL,-100.00,1700.25\n"
         "L4,2026-02-02,CASH PAID IN,250.10,1950.35\n")
BUCKETS = ["M2", "M19", "M20", "M21", "M22", "M23", "M24", "M25"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")  # cash paid in is a cash deposit (R55)
    return root


def _case(tmp_path, body=SMALL, *, label="t19", account="main", case_id=None):
    if case_id is None:
        case_id = store.new_case(label=label)
    folder = store.case_folder(case_id)
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
        return r.status, r.getheader("Content-Type"), r.read()
    finally:
        conn.close()


def _api(srv) -> dict:
    status, kind, body = _get(srv, "/api/case")
    assert status == 200 and kind == "application/json"
    return json.loads(body)


def _page(srv) -> str:
    status, kind, body = _get(srv, "/")
    assert status == 200 and kind.startswith("text/html")
    return body.decode("utf-8")


def _pence(display: str) -> int:
    text = display.replace("£", "").replace(",", "")
    return int(Decimal(text) * 100)


def _break_reconciliation(monkeypatch, line_id="L2", category=Category.SUPPLIER):
    """Make one credit (or each of several) land in no money-in bucket, as a bug in the
    categoriser would: the figures layer then refuses the case (R3)."""
    real = overrides.categorise
    ids = {line_id} if isinstance(line_id, str) else set(line_id)

    def broken(statement, *args, **kwargs):
        out = real(statement, *args, **kwargs)
        for t in statement.transactions:
            if t.id in ids:
                t.category = category
        return out

    monkeypatch.setattr(overrides, "categorise", broken)


# --- the summary through the API ----------------------------------------------------------


@pytest.mark.contracts
def test_the_api_gives_the_figures_layer_s_figures_for_each_account(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    data = _api(serve(case_id))
    assert data["case"] == case_id and data["label"] == "t19" and data["state"] == "shown"
    assert data["reconciliation"] is None and data["refusal"] is None
    [acc] = data["accounts"]
    assert acc["account"] == "main" and acc["line_count"] == 4
    assert acc["period"] == {"start": "2026-01-05", "end": "2026-02-02"}
    want = {m.id: m for m in overrides.case_figures(folder)["main"].metrics}
    # Every figure is the figures layer's own, in its order, value and evidence count.
    assert [f["id"] for f in acc["figures"]] == [f"M{i}" for i in range(1, 29)]
    for f in acc["figures"]:
        m = want[f["id"]]
        assert (f["name"], f["value"], f["unit"], f["lines"]) == (m.name, m.value, m.unit, len(m.evidence))
        assert f["display"] == m.display_exact()
    mi = acc["money_in"]
    assert [b["id"] for b in mi["buckets"]] == BUCKETS
    assert mi["total"]["id"] == "M17" and mi["cash_within_trading"]["id"] == "M18"
    # The breakdown as shown, to the penny, adds up to total money in as shown (R1, R2).
    assert sum(_pence(b["display"]) for b in mi["buckets"]) == _pence(mi["total"]["display"]) == 105035
    assert mi["total"]["display"] == "£1,050.35"
    assert mi["cash_within_trading"]["display"] == "£250.10"


@pytest.mark.contracts
def test_the_api_gives_the_monthly_summary_with_each_cell(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    acc = _api(serve(case_id))["accounts"][0]
    want = overrides.case_figures(folder)["main"].monthly
    assert [r["month"] for r in acc["monthly"]] == ["2026-01", "2026-02"]
    for row, w in zip(acc["monthly"], want, strict=True):
        assert row["part_month"] is True  # the period starts on the 5th and ends on the 2nd
        for key, _ in summary.CELLS:
            cell, wc = row[key], getattr(w, key)
            assert (cell["value"], cell["kind"], cell["lines"]) == (wc.value, wc.kind, len(wc.evidence))
    jan = acc["monthly"][0]
    assert jan["money_in"]["display"] == "£800.25"
    assert jan["money_out"]["display"] == "£100.00" and jan["money_out"]["kind"] == "outflow"
    assert jan["days_overdrawn"]["display"] == "0"


@pytest.mark.contracts
def test_a_case_with_no_statement_shows_that_and_no_figures(home, serve):
    case_id = store.new_case(label="nothing yet")
    srv = serve(case_id)
    data = _api(srv)
    assert data["state"] == "empty" and data["accounts"] == []
    page = _page(srv)
    assert "No statement has been imported" in page and "M17" not in page


@pytest.mark.contracts
def test_an_override_is_shown_on_the_figures_that_rest_on_it(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    overrides.add_override(folder, "L2", "director_funding", reason="director's loan", name="A Person")
    acc = _api(serve(case_id))["accounts"][0]
    by_id = {f["id"]: f for f in acc["figures"]}
    assert by_id["M20"]["overridden"] and by_id["M20"]["display"] == "£300.25"
    assert not by_id["M18"]["overridden"]
    assert acc["monthly"][0]["money_in"]["overridden"] is True


# --- the summary on the page --------------------------------------------------------------


class _Tags(HTMLParser):
    """Every tag and attribute of a page, and its text, as a browser would parse them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, tuple]] = []
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, tuple(attrs)))

    handle_startendtag = handle_starttag

    def handle_data(self, data):
        self.text.append(data)


def _parse(page: str) -> _Tags:
    p = _Tags()
    p.feed(page)
    p.close()
    return p


@pytest.mark.contracts
def test_the_page_shows_the_breakdown_the_figures_and_the_monthly_summary(home, tmp_path, serve):
    case_id, _ = _case(tmp_path)
    page = _page(serve(case_id))
    p = _parse(page)
    classes = {dict(a).get("class") for t, a in p.tags if t == "table"}
    assert {"breakdown", "figures", "monthly"} <= classes
    text = " ".join(p.text)
    for fid in [*BUCKETS, "M17", "M18", "M28"]:
        assert fid in text
    assert "£1,050.35" in text and "£250.10" in text and "2026-02" in text and "part month" in text
    assert "Total money in" in text and "Cash deposits within trading receipts" in text
    # Only the case's own figures: nothing computed in the page; no script or style inline.
    scripts = [a for t, a in p.tags if t == "script"]
    assert len(scripts) == 1 and dict(scripts[0])["src"].startswith("/static/app.js")
    assert not any(k == "style" or k.startswith("on") for _, a in p.tags for k, _ in a)


# --- R3: no figures when the buckets do not reconcile -------------------------------------


@pytest.mark.contracts
def test_a_case_that_does_not_reconcile_shows_no_figures_and_names_difference_and_lines(
        home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)
    _break_reconciliation(monkeypatch)
    srv = serve(case_id)
    data = _api(srv)
    assert data["state"] == "not_reconciled" and data["accounts"] == []
    rec = data["reconciliation"]
    assert rec["difference_pence"] == 30025 and rec["difference"] == "300.25"
    assert [(x["account"], x["id"], x["date"], x["description"], x["amount"]) for x in rec["lines"]] == [
        ("main", "L2", "2026-01-06", "FPS CREDIT J BLOGGS", "£300.25")]
    page = _page(srv)
    p = _parse(page)
    text = " ".join(p.text)
    assert "30025 pence" in text and "£300.25" in text and "L2" in text
    # Nothing is shown: no breakdown, no figure, no monthly cell, not even a bucket's name.
    assert not {"breakdown", "figures", "monthly"} & {dict(a).get("class") for t, a in p.tags}
    for shown in ("M17", "M2 ", "Total money in", "£1,050.35", "£250.10", "Monthly summary"):
        assert shown not in text, shown


@pytest.mark.contracts
def test_a_debit_counted_as_trading_receipts_is_reported_too(home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)
    _break_reconciliation(monkeypatch, "L3", Category.REVENUE_CARD)
    rec = _api(serve(case_id))["reconciliation"]
    assert rec["difference_pence"] == 10000 and [x["id"] for x in rec["lines"]] == ["L3"]
    assert rec["lines"][0]["amount"] == "-£100.00"


@pytest.mark.contracts
def test_one_account_that_does_not_reconcile_hides_every_account(home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)
    other = ("id,date,description,amount,balance\n"
             "X1,2026-04-01,INTEREST,1.00,11.00\n"
             "X2,2026-04-02,FPS CREDIT A N OTHER,20.00,31.00\n")
    _case(tmp_path, other, account="savings", case_id=case_id)
    _break_reconciliation(monkeypatch, "X2")
    data = _api(serve(case_id))
    assert data["state"] == "not_reconciled" and data["accounts"] == []
    assert [(x["account"], x["id"]) for x in data["reconciliation"]["lines"]] == [("savings", "X2")]


@pytest.mark.contracts
def test_every_account_that_does_not_reconcile_is_reported_with_its_difference_and_lines(
        home, tmp_path, serve, monkeypatch):
    """T19 review 2: with two accounts failing, both differences and every line are reported,
    not only the first account's (R3)."""
    case_id, _ = _case(tmp_path)
    other = ("id,date,description,amount,balance\n"
             "X1,2026-04-01,INTEREST,1.00,11.00\n"
             "X2,2026-04-02,FPS CREDIT A N OTHER,20.00,31.00\n")
    _case(tmp_path, other, account="savings", case_id=case_id)
    _break_reconciliation(monkeypatch, ["L2", "X2"])
    srv = serve(case_id)
    data = _api(srv)
    assert data["state"] == "not_reconciled" and data["accounts"] == []
    rec = data["reconciliation"]
    assert [(x["account"], x["id"]) for x in rec["lines"]] == [("main", "L2"), ("savings", "X2")]
    assert [(a["account"], a["difference_pence"], a["lines"]) for a in rec["accounts"]] == [
        ("main", 30025, 1), ("savings", 2000, 1)]
    assert rec["difference_pence"] == 32025
    text = " ".join(_parse(_page(srv)).text)
    assert "30025 pence" in text and "2000 pence" in text and "L2" in text and "X2" in text
    for shown in ("M17", "Total money in", "Monthly summary"):
        assert shown not in text, shown


@pytest.mark.contracts
def test_case_figures_reports_every_account_that_does_not_reconcile(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    other = ("id,date,description,amount,balance\n"
             "X1,2026-04-01,INTEREST,1.00,11.00\n"
             "X2,2026-04-02,FPS CREDIT A N OTHER,20.00,31.00\n")
    _case(tmp_path, other, account="savings", case_id=case_id)
    _break_reconciliation(monkeypatch, ["L2", "X2"])
    with pytest.raises(metrics.ReconciliationError) as e:
        overrides.case_figures(folder)
    assert [(f.account, f.difference_pence, f.lines) for f in e.value.failures] == [
        ("main", 30025, ["L2"]), ("savings", 2000, ["X2"])]
    assert e.value.lines == ["L2", "X2"] and e.value.difference_pence == 32025
    assert "main" in str(e.value) and "savings" in str(e.value)


@pytest.mark.contracts
def test_a_case_whose_files_are_refused_shows_no_figures(home, tmp_path, serve):
    case_id, folder = _case(tmp_path)
    overrides.add_override(folder, "L2", "director_funding", reason="loan", name="A Person")
    path = folder / overrides.OVERRIDES
    path.chmod(0o600)
    path.write_text(path.read_text().replace("loan", "LOAN"))  # changed by hand
    srv = serve(case_id)
    data = _api(srv)
    assert data["state"] == "refused" and data["accounts"] == []
    assert "changed by hand" in data["refusal"]
    assert "M17" not in _page(srv)


# --- P8: description text only in escaped form -------------------------------------------

# Characters the HTML parser gives meaning to, beside ordinary statement wording.
PIECES = ["<", ">", "&", '"', "'", "<script>", "</script>", "<img src=x onerror=alert(1)>", "&amp;",
          "&#60;", "<!--", "-->", "<b>", "</td>", "</table>", "javascript:", " style=", "=", "/", "\\",
          "__CMA_TOKEN__", "__CMA_SUMMARY__", "£", "é", "STRIPE", "CASH PAID IN", "J BLOGGS", "LTD", " "]


def _description(rng: random.Random) -> str:
    text = "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 8)))
    return f"P{rng.randint(0, 999)} {text}".strip()


def _random_statement(rng: random.Random) -> tuple[str, list[tuple[str, str]]]:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["id", "date", "description", "amount", "balance"])
    balance, lines = 0, []
    for n in range(rng.randint(1, 12)):
        pence = rng.choice([1, -1]) * rng.randint(1, 500_000)
        balance += pence
        desc = _description(rng)
        lines.append((f"L{n + 1}", desc))
        w.writerow([f"L{n + 1}", f"2026-03-{n + 1:02d}", desc, f"{pence / 100:.2f}", f"{balance / 100:.2f}"])
    return out.getvalue(), lines


def _assert_only_escaped(page: str, texts: list[str], *, shown: bool) -> None:
    allowed = {"html", "head", "meta", "title", "link", "script", "body", "header", "h1", "h2", "h3", "p",
               "main", "section", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "span"}
    p = _parse(page)
    # No text adds markup: every tag is one the workspace writes, with no handler or style.
    assert {t for t, _ in p.tags} <= allowed
    assert not any(k == "style" or k.startswith("on") for _, a in p.tags for k, _ in a)
    assert [t for t, _ in p.tags].count("script") == 1
    page_text = "".join(p.text)
    for text in texts:
        if escape(text, quote=True) != text:  # never in raw form, outside its escaped form
            assert text not in page.replace(escape(text, quote=True), "\0"), text
        if shown:
            assert escape(text, quote=True) in page, text  # only in escaped form
            assert text in page_text  # and a browser reads it back as the text it is


@pytest.mark.invariants
@pytest.mark.parametrize("seed", range(25))
def test_p8_the_workspace_html_holds_description_text_only_escaped(home, tmp_path, serve, monkeypatch, seed):
    rng = random.Random(seed)
    body, lines = _random_statement(rng)
    label, business = _description(rng), _description(rng)
    case_id = store.new_case(label=label)
    folder = store.case_folder(case_id)
    store.update_profile(folder, lambda p: {**p, "business_name": business})
    _case(tmp_path, body, case_id=case_id)
    srv = serve(case_id)
    # As the figures layer gives it: no description is in the summary, and none is raw.
    page = _page(srv)
    _assert_only_escaped(page, [d for _, d in lines], shown=False)
    _assert_only_escaped(page, [label, business], shown=True)
    assert srv.token in page  # the page still names its own token, once, where it should
    # When the case does not reconcile, the lines involved are shown with their descriptions.
    credits = [(i, d) for (i, d), row in zip(lines, body.splitlines()[1:], strict=True)
               if not next(csv.reader([row]))[3].startswith("-")]
    target = credits[0] if credits else lines[0]
    _break_reconciliation(monkeypatch, target[0], Category.SUPPLIER if credits else Category.REVENUE_CARD)
    page = _page(srv)
    assert _api(srv)["state"] == "not_reconciled"
    _assert_only_escaped(page, [target[1], label, business], shown=True)
    _assert_only_escaped(page, [d for i, d in lines if i != target[0]], shown=False)


@pytest.mark.invariants
def test_p8_the_api_carries_description_text_as_json_data_only(home, tmp_path, serve, monkeypatch):
    desc = '<script>alert("x")</script> & </td>'
    case_id, _ = _case(tmp_path, SMALL.replace("FPS CREDIT J BLOGGS", f'"{desc.replace(chr(34), 2 * chr(34))}"'))
    _break_reconciliation(monkeypatch)
    srv = serve(case_id)
    status, kind, body = _get(srv, "/api/case")
    assert status == 200 and kind == "application/json"  # never sniffed as HTML: every reply is nosniff
    assert json.loads(body)["reconciliation"]["lines"][0]["description"] == desc
    page = _page(srv)
    assert desc not in page and escape(desc, quote=True) in page


@pytest.mark.contracts
def test_render_takes_its_markup_from_the_module_alone():
    s = {"case": "c", "label": "<b>x</b>", "business_name": None, "state": "refused",
         "accounts": [], "reconciliation": None, "refusal": "<img src=x onerror=1>"}
    html = summary.render(s)
    assert "<b>" not in html and "<img" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "&lt;img src=x onerror=1&gt;" in html


@pytest.mark.contracts
def test_case_figures_names_the_account_of_a_case_that_does_not_reconcile(home, tmp_path, monkeypatch):
    _, folder = _case(tmp_path)
    _break_reconciliation(monkeypatch)
    with pytest.raises(metrics.ReconciliationError) as e:
        overrides.case_figures(folder)
    assert e.value.account == "main" and e.value.lines == ["L2"]
    assert e.value.statement.by_id()["L2"].category == Category.SUPPLIER


# --- failures the summary meets -------------------------------------------------------------


@pytest.mark.contracts
def test_a_ruleset_that_cannot_load_is_a_refusal_with_no_figures(home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)
    monkeypatch.setenv("CMA_RULESET", "v9")
    srv = serve(case_id)
    data = _api(srv)
    assert data["state"] == "refused" and data["accounts"] == []
    assert "CMA_RULESET" in data["refusal"]
    page = _page(srv)
    assert "No figures are shown" in page and "M17" not in page


@pytest.mark.contracts
def test_a_failure_no_refusal_foresees_is_answered_500_naming_nothing(home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("FPS CREDIT J BLOGGS")

    monkeypatch.setattr(summary, "case_summary", boom)
    srv = serve(case_id)
    for path in ("/", "/api/case"):
        status, _, body = _get(srv, path)
        assert status == 500 and body == b"The case could not be shown.\n"


@pytest.mark.contracts
def test_a_monthly_cell_that_is_n_a_shows_its_reason(home, tmp_path):
    """T19 review 2: a monthly cell the figures layer cannot carry to the penny shows n/a
    with the reason it carries, escaped (R2, P8)."""
    case_id, folder = _case(tmp_path)
    data = summary.case_summary(folder, case_id)
    cell = data["accounts"][0]["monthly"][0]["average_balance"]
    cell.update(value=None, display="n/a", note="Too large to report <b>to the penny</b>.")
    page = summary.render(data)
    assert "n/a <span class=\"note\">Too large to report &lt;b&gt;to the penny&lt;/b&gt;.</span>" in page
    assert "<b>" not in page


# --- hardening: read-only, and the token before any computation ---------------------------


def _tree(folder):
    return {p.relative_to(folder).as_posix(): (p.stat().st_size, p.read_bytes())
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.mark.contracts
def test_showing_the_summary_writes_nothing_to_the_case(home, tmp_path, serve, monkeypatch):
    """GET / and /api/case are read-only: the case folder is byte-for-byte unchanged, shown,
    refused for reconciliation, or asked for many times (idempotent)."""
    case_id, folder = _case(tmp_path)
    overrides.add_override(folder, "L2", "director_funding", reason="loan", name="A Person")
    before = _tree(folder)
    srv = serve(case_id)
    first = _api(srv)
    for _ in range(3):
        _page(srv)
        assert _api(srv) == first
    _break_reconciliation(monkeypatch)
    _page(srv)
    assert _api(srv)["state"] == "not_reconciled"
    assert _tree(folder) == before


@pytest.mark.contracts
def test_no_summary_is_computed_for_a_request_without_the_token(home, tmp_path, serve, monkeypatch):
    case_id, _ = _case(tmp_path)
    calls = []
    monkeypatch.setattr(summary, "case_summary", lambda *a, **k: calls.append(a) or {})
    srv = serve(case_id)
    for path in ("/", "/api/case"):
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        try:
            conn.request("GET", path, headers={"Host": f"127.0.0.1:{srv.port}"})
            r = conn.getresponse()
            assert r.status == 403 and b"L2" not in r.read()
        finally:
            conn.close()
    assert calls == []


@pytest.mark.contracts
def test_an_override_that_puts_a_line_right_lets_the_case_be_shown(home, tmp_path, serve, monkeypatch):
    """R3, R17: the case was refused whenever the figures without overrides did not reconcile,
    a comparison made only to mark figures, so an override that puts a line right could never
    let the case be shown. The figures as overridden decide; the comparison only marks."""
    case_id, folder = _case(tmp_path)
    real = overrides.categorise

    def broken_unless_overridden(statement, *args, **kwargs):  # a fault an override corrects
        out = real(statement, *args, **kwargs)
        if "L2" not in (kwargs.get("overrides") or {}):
            next(t for t in statement.transactions if t.id == "L2").category = Category.SUPPLIER
        return out

    monkeypatch.setattr(overrides, "categorise", broken_unless_overridden)
    assert _api(serve(case_id))["state"] == "not_reconciled"
    overrides.add_override(folder, "L2", "director_funding", reason="checked the paying-in slip", name="Pat")
    data = _api(serve(case_id))
    assert data["state"] == "shown" and data["reconciliation"] is None
    figures = {f["id"]: f for f in data["accounts"][0]["figures"]}
    assert figures["M20"]["overridden"] and figures["M17"]["overridden"]  # L2 is among their lines
