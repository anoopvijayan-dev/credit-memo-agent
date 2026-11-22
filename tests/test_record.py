"""T26: the pack's self-contained, offline record.html (C6; R2, R16, R25, R26, R34, R48, R56,
P8, P20).

record.html is written once at sign-off beside memo.md, evidence.json and monthly.csv. It
refers to nothing outside itself, every figure, bucket and monthly cell links to a section
of the record that lists exactly the lines behind it, the illustrative label sits with every
outcome, no outcome or rule result appears with show_policy_verdict off, and it shows the
sign-off. A demo case shows the banner in the workspace and the stamp in the memo and the
record (R48)."""

import csv
import hashlib
import http.client
import io
import json
import random
import re
from html import escape
from html.parser import HTMLParser

import pytest

from cma import case as caselog
from cma import overrides, pack, policy, record, store
from cma.review import server as rs
from cma.review import summary

STATEMENT = ("id,date,description,amount,balance\n"
             "L1,2026-01-05,CARD SETTLEMENT STRIPE,500.00,1500.00\n"
             "L2,2026-01-06,FPS CREDIT J BLOGGS,300.25,1800.25\n"
             "L3,2026-01-07,IWOCA LOAN REPAYMENT,-100.00,1700.25\n"
             "L4,2026-02-02,CASH PAID IN,250.10,1950.35\n"
             "L5,2026-02-20,DD COUNCIL,-2100.00,-149.65\n"
             "L6,2026-03-03,CARD SETTLEMENT SUMUP,400.00,250.35\n")
OTHER = ("id,date,description,amount,balance\n"
         "S1,2026-01-03,CARD SETTLEMENT SUMUP,900.00,900.00\n"
         "S2,2026-01-20,IWOCA LOAN REPAYMENT,-120.00,780.00\n")
OUTCOME_WORD = re.compile(r"\b(pass|refer|fail|PASS|REFER|FAIL)\b")
HOSTILE = ['<script>alert("x")</script> STRIPE', "CARD SETTLEMENT STRIPE & <b>bold</b>",
           "\"onmouseover=\"alert(1)\" FPS CREDIT", "'><img src=x onerror=alert(1)>",
           "=HYPERLINK(\"http://evil.example\") CASH PAID IN", "A &amp; B <!-- c --> </pre></section>"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "cma-home"
    monkeypatch.setenv("CMA_HOME", str(root))
    monkeypatch.setenv("CMA_RULESET", "v2")
    return root


def _setting(home, shown):
    home.mkdir(parents=True, exist_ok=True)
    if shown is not None:
        (home / "firm.yaml").write_text(f"show_policy_verdict: {'true' if shown else 'false'}\n", encoding="utf-8")


def _case(tmp_path, accounts=(("main", STATEMENT),), demo=False):
    case_id = store.new_case(label="t26", demo=demo)
    folder = store.case_folder(case_id)
    for account, body in accounts:
        src = tmp_path / f"{account}.csv"
        src.write_text(body, encoding="utf-8")
        store.import_file(folder, src, "csv", account=account)
    return case_id, folder


def _sign(folder, case_id, name="Ann Smith"):
    p = pack.build_preview(folder, case_id)
    s = pack.sign_off(folder, case_id, p.snapshot, name)
    return p, s, (s.pack / "record.html").read_text(encoding="utf-8")


def _statement(descriptions):
    """A five-column statement whose credits carry `descriptions`, the balance walked in pence."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "date", "description", "amount", "balance"])
    pence = 0
    for n, d in enumerate(descriptions, start=1):
        pence += 1000 + n
        w.writerow([f"H{n}", f"2026-01-{n:02d}", d, f"{(1000 + n) / 100:.2f}", f"{pence // 100}.{pence % 100:02d}"])
    return buf.getvalue()


class _Elements(HTMLParser):
    """Each element with its attributes, its text and the elements around it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.done = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        el = {"tag": tag, "attrs": a, "class": (a.get("class") or "").split(), "id": a.get("id"),
              "text": [], "within": list(self.stack), "children": []}
        if self.stack:
            self.stack[-1]["children"].append(el)
        if tag in ("meta", "br", "input", "img", "link"):
            self.done.append({**el, "text": ""})
            return
        self.stack.append(el)

    def handle_endtag(self, tag):
        while self.stack:
            el = self.stack.pop()
            el["text"] = "".join(el["text"])
            self.done.append(el)
            if self.stack:
                self.stack[-1]["text"].append(el["text"])
            if el["tag"] == tag:
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"].append(data)


def _elements(html):
    p = _Elements()
    p.feed(html)
    p.close()
    return p.done


def _lines_listed(section):
    """The line ids in a lines-behind section's Lines table, in order."""
    tables = [c for c in _walk(section) if c["tag"] == "table" and "lines" in c["class"]]
    assert len(tables) == 1
    out = []
    for tr in [c for c in _walk(tables[0]) if c["tag"] == "tr"]:
        tds = [c for c in tr["children"] if c["tag"] == "td"]
        if tds:
            out.append(tds[0]["text"])
    return out


def _walk(el):
    for c in el["children"]:
        yield c
        yield from _walk(c)


# --- R26: the pack holds the record, self-contained and offline ----------------------------


@pytest.mark.contracts
def test_r26_the_pack_writes_record_html_once_with_its_hash_in_the_case_log(home, tmp_path):
    case_id, folder = _case(tmp_path)
    p, s, html = _sign(folder, case_id)
    assert "record.html" in pack.PACK_FILES and (s.pack / "record.html").is_file()
    signed = caselog.for_case(folder).entries()[-1].data
    assert signed["files"]["record.html"] == hashlib.sha256((s.pack / "record.html").read_bytes()).hexdigest()
    assert html.startswith("<!doctype html>") and p.snapshot in html and case_id in html


@pytest.mark.contracts
@pytest.mark.parametrize("shown", [True, False])
def test_r26_the_record_refers_to_nothing_outside_itself(home, tmp_path, shown):
    _setting(home, shown)
    case_id, folder = _case(tmp_path, (("main", STATEMENT), ("other", OTHER)))
    _p, _s, html = _sign(folder, case_id)
    lowered = html.lower()
    for banned in ("http:", "https:", "ftp:", "file:", "data:", "javascript:", "<script", "<link", "<img",
                   "<iframe", "<object", "<embed", "@import", "url(", " src=", "srcset", "<base", "<form"):
        assert banned not in lowered, banned
    els = _elements(html)
    ids = {e["id"] for e in els if e["id"]}
    hrefs = [e["attrs"]["href"] for e in els if "href" in e["attrs"]]
    assert hrefs and all(h.startswith("#") for h in hrefs)
    assert {h[1:] for h in hrefs} <= ids  # every link lands in the record itself
    csp = [e for e in els if e["tag"] == "meta" and e["attrs"].get("http-equiv") == "Content-Security-Policy"]
    assert len(csp) == 1 and "default-src 'none'" in csp[0]["attrs"]["content"]


@pytest.mark.contracts
def test_r26_r16_every_figure_bucket_and_cell_links_to_exactly_its_lines(home, tmp_path):
    case_id, folder = _case(tmp_path, (("main", STATEMENT), ("other", OTHER)))
    p, _s, html = _sign(folder, case_id)
    els = _elements(html)
    by_id = {e["id"]: e for e in els if e["id"]}
    links = {e["attrs"]["href"][1:] for e in els if e["tag"] == "a" and e["attrs"].get("href", "").startswith("#lines-")}
    figures = overrides.case_figures(folder)
    for a in p.accounts:
        f = figures[a.account]
        targets = [({"figure": m.id}, m.evidence, m) for m in f.metrics]
        targets += [({"month": r.month, "cell": key}, getattr(r, key).evidence, None)
                    for r in f.monthly for key, _ in summary.CELLS]
        assert len(targets) > 28
        for target, evidence, m in targets:
            anchor = record.anchor(a.account, **target)
            assert anchor in links, anchor  # the account's tables link to it
            section = by_id[anchor]
            assert "lines-behind" in section["class"]
            assert _lines_listed(section) == list(evidence), anchor  # exactly its lines, in order
            if m is not None:
                assert m.display_exact() in section["text"]  # to the penny (R2)
    # a sum figure's section shows its lines' total, which is the figure
    m2 = by_id[record.anchor("main", figure="M2")]
    m2_figure = next(m for m in figures["main"].metrics if m.id == "M2")
    assert f"Total of the lines' signed amounts: {m2_figure.display_exact()}, the figure." in m2["text"]
    m8 = by_id[record.anchor("main", figure="M8")]  # an outflow: its lines total it negated
    assert "the figure negated" in m8["text"]
    # every line's description appears in the record, escaped, beside its id
    assert "IWOCA LOAN REPAYMENT" in by_id[record.anchor("main", figure="M8")]["text"]


@pytest.mark.contracts
def test_r22_r24_the_record_shows_the_sign_off_and_never_approves_the_loan(home, tmp_path):
    case_id, folder = _case(tmp_path)
    _p, s, html = _sign(folder, case_id, "  Ann   Smith ")
    section = next(e for e in _elements(html) if e["id"] == "sign-off")
    text = section["text"]
    assert "Ann Smith" in text and s.os_user in text and s.at in text and s.snapshot in text
    assert pack.SIGN_WORDING in text
    assert "approve the loan" not in html.replace("does not approve the loan", "")
    assert (s.pack / "memo.md").read_text(encoding="utf-8") in escape_text_of(html)


def escape_text_of(html):
    """The record's memo-ready sections, read back from its pre element."""
    return next(e for e in _elements(html) if e["tag"] == "pre" and "memo" in e["class"])["text"]


# --- P8: description text only in escaped form ---------------------------------------------


@pytest.mark.invariants
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_p8_record_html_holds_description_text_only_escaped(home, tmp_path, seed):
    rng = random.Random(seed)
    descriptions = [rng.choice(HOSTILE) + " " + "".join(rng.choice("<>&\"'=/abcXYZ") for _ in range(12))
                    for _ in range(6)]
    case_id, folder = _case(tmp_path, (("main", _statement(descriptions)),))
    _p, _s, html = _sign(folder, case_id)
    for d in descriptions:
        if escape(d, quote=True) == d:
            continue
        assert d not in html  # never raw
        assert escape(d, quote=True) in html  # always escaped
    assert "<script" not in html.lower() and "<img" not in html.lower()
    # read back as text, every description is there whole
    texts = "".join(e["text"] for e in _elements(html) if e["tag"] == "td")
    assert all(d in texts for d in descriptions)


# --- P20: the label wherever an outcome appears; nothing with the setting off -------------


@pytest.mark.invariants
@pytest.mark.parametrize("shown", [None, True], ids=["default", "on"])
@pytest.mark.parametrize("accounts", [(("main", STATEMENT),), (("main", STATEMENT), ("other", OTHER))],
                         ids=["one-account", "two-accounts"])
def test_p20_record_with_show_policy_verdict_on(home, tmp_path, shown, accounts):
    _setting(home, shown)
    case_id, folder = _case(tmp_path, accounts)
    p, _s, html = _sign(folder, case_id)
    els = _elements(html)
    panels = [e for e in els if "policy-check" in e["class"]]
    memo = [e for e in els if e["tag"] == "pre" and "memo" in e["class"]]
    assert len(panels) == len(p.accounts) and len(memo) == 1
    for panel in panels:
        assert policy.ILLUSTRATIVE_LABEL in panel["text"]
    for a in p.accounts:
        panel = next(x for x in panels if x["attrs"].get("data-account") == a.account)
        assert f"Overall outcome: {a.policy.overall.value.upper()}" in panel["text"]
        for r in a.policy.rules:
            assert r.rule_id in panel["text"] and r.detail in panel["text"]
    # the memo's own policy section carries the label with its outcomes
    assert OUTCOME_WORD.search(memo[0]["text"]) and policy.ILLUSTRATIVE_LABEL in memo[0]["text"]
    # every element that shows an outcome sits inside a panel that holds the label
    for e in els:
        if "outcome" in e["class"] or "rule-outcome" in e["class"]:
            assert any(id(w) in {id(x) for x in panels} for w in e["within"]), e["text"]
    # and nowhere else does an outcome appear
    outside, cut = re.subn(r'<section class="policy-check".*?</section>', "", html, flags=re.S)
    outside, cut_memo = re.subn(r'<pre class="memo">.*?</pre>', "", outside, flags=re.S)
    assert cut == len(panels) and cut_memo == 1
    assert not OUTCOME_WORD.search(re.sub(r"<[^>]*>", " ", outside))
    rec = [e for e in els if e["attrs"].get("data-field") == "recommendation"]
    assert len(rec) == 1 and rec[0]["text"].strip() == ""  # blank for the underwriter (R24)


@pytest.mark.invariants
@pytest.mark.parametrize("accounts", [(("main", STATEMENT),), (("main", STATEMENT), ("other", OTHER))],
                         ids=["one-account", "two-accounts"])
def test_p20_record_with_show_policy_verdict_off_holds_no_outcome_or_rule_result(home, tmp_path, accounts):
    _setting(home, False)
    case_id, folder = _case(tmp_path, accounts)
    _p, _s, html = _sign(folder, case_id)
    assert not OUTCOME_WORD.search(html)
    assert not [e for e in _elements(html) if {"policy-check", "outcome", "rule-outcome"} & set(e["class"])]
    assert 'id="policy-checks"' not in html and "Overall outcome" not in html
    assert policy.ILLUSTRATIVE_LABEL not in html and pack.POLICY_TITLE not in html
    for f in overrides.case_figures(folder).values():
        for r in policy.evaluate(f.metrics).rules:  # no rule result: neither its sentence nor its rule
            for text in (r.detail, r.description):
                assert text not in html and escape(text, quote=True) not in html
            assert f">{r.rule_id}<" not in html and f"[{r.rule_id}]" not in html
    rec = [e for e in _elements(html) if e["attrs"].get("data-field") == "recommendation"]
    assert len(rec) == 1 and rec[0]["text"].strip() == ""


# --- R48: a demo case shows the banner and the stamp ---------------------------------------


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
        return r.status, r.read().decode("utf-8")
    finally:
        conn.close()


@pytest.mark.contracts
@pytest.mark.parametrize("demo", [True, False])
def test_r48_a_demo_case_shows_the_banner_and_stamp_in_the_workspace_memo_and_record(home, tmp_path, serve, demo):
    case_id, folder = _case(tmp_path, demo=demo)
    srv = serve(case_id)
    stamp = escape(pack.DEMO_STAMP, quote=True)
    banner = 'id="demo-banner"'
    for path in ("/", "/lines?account=main&figure=M2"):
        status, page = _get(srv, path)
        assert status == 200
        assert (banner in page and stamp in page) is demo, path
        if demo:  # the banner comes first in the page's main element
            main = '<main id="workspace">'
            assert page[page.index(main) + len(main):].startswith(f'<section {banner}'), path
    status, body = _get(srv, "/api/case")
    assert status == 200 and json.loads(body)["demo"] is demo
    _p, s, html = _sign(folder, case_id)
    memo = (s.pack / "memo.md").read_text(encoding="utf-8")
    assert (pack.DEMO_STAMP in memo) is demo
    assert (banner in html and stamp in html) is demo
    if demo:
        assert html.index(banner) < html.index('id="sign-off"')  # the stamp is the record's first section
    ev = json.loads((s.pack / "evidence.json").read_text(encoding="utf-8"))
    assert ev["demo"] is demo


# --- Partial failure: a record that cannot be rendered writes no pack and records nothing --


@pytest.mark.contracts
def test_r26_r42_a_record_that_fails_to_render_writes_no_pack_and_records_nothing(home, tmp_path, monkeypatch):
    case_id, folder = _case(tmp_path)
    p = pack.build_preview(folder, case_id)
    before = [e.event for e in caselog.for_case(folder).entries()]

    def broken(*_a, **_k):
        raise RuntimeError("render failed")

    monkeypatch.setattr(record, "render", broken)
    with pytest.raises(RuntimeError):
        pack.sign_off(folder, case_id, p.snapshot, "Ann")
    packs = folder / "packs"
    assert not packs.exists() or not any(packs.iterdir())  # no pack, no .part- folder left
    assert [e.event for e in caselog.for_case(folder).entries()] == before
    monkeypatch.undo()
    s = pack.sign_off(folder, case_id, p.snapshot, "Ann")  # the same preview then signs off
    assert (s.pack / "record.html").is_file()
