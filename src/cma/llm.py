"""Model providers behind one tiny interface.

The memo graph only needs `complete(system, user) -> str`. `FakeLLM` writes a memo
from the metrics table with no network call, so tests and CI run without keys. It
also has a `hallucinate` switch so the grounding guard can be tested against a memo
that invents a number.

The hosted providers check the firm's offline lock (firm.yaml, on by default) before
anything else, so under the lock no client is created and no connection is attempted.
They check it again before every request, against the lock value the run read from
firm.yaml at its start (or firm.yaml read then, outside a run), and the SDK clients are
built with their own retries off, so every request the SDK sends passes that check.
"""

from __future__ import annotations

import os
import re
from contextvars import ContextVar
from typing import Protocol

from . import firm


class LLMError(RuntimeError):
    """The model call failed or came back unusable (cut off, empty)."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class FakeLLM:
    name = "fake"
    model = "offline-template"

    def __init__(self, hallucinate: bool = False):
        self.hallucinate = hallucinate

    def complete(self, system: str, user: str) -> str:
        # Pull the metric and rule tables out of the prompt and write a plain memo that
        # keeps the contract: figures as shown, each followed by its tag.
        rows = re.findall(r"^\| (M\d+) \| ([^|]+) \| ([^|]+) \|", user, re.MULTILINE)
        by_id = {mid: (name.strip(), val.strip()) for mid, name, val in rows}
        rules = re.findall(r"^\| (R\d+) \| ([^|]+) \| (M\d+) \| ([^|]+) \| (\w+) \|", user, re.MULTILINE)
        overall = re.search(r"^\| overall \|[^|]*\|[^|]*\|[^|]*\| (\w+) \|", user, re.MULTILINE)
        outcome = overall.group(1) if overall else "refer"

        def q(mid: str) -> str:
            return f"{by_id[mid][1]} [{mid}]"

        flagged = []
        for rid, _desc, _mid, req, result in rules:
            if result != "pass":
                limit = re.match(r"\S+ ([^;\s]+)", req.strip())
                flagged.append(f"{rid} (limit {limit.group(1)} [{rid}]) is {result}")
        detail = "; ".join(flagged)

        memo = f"""## Business overview
The statements cover {q('M1')} months. Total revenue over the period was {q('M2')}, averaging {q('M3')} a month.

## Revenue
Revenue moved {q('M4')}, comparing the latest quarter with the earliest. The largest single payer accounted for {q('M15')} of transfer revenue. Unclassified money in was {q('M16')} of inflows.

## Cash position
The lowest balance reached was {q('M5')}, and the account closed overdrawn on {q('M6')} days. Net operating cash flow was {q('M9')}.

## Obligations and conduct
Loan repayments totalled {q('M8')} and owner drawings {q('M11')}, giving debt service cover after drawings of {q('M10')}. Returned direct debits in the period: {q('M7')}. VAT paid was {q('M12')} with late or missed payments of {q('M13')}; PAYE paid was {q('M14')}.

## Recommendation
Policy outcome: {outcome}. {'The case meets policy and can proceed to approval.' if outcome == 'pass' else 'The case needs an analyst decision before any offer: ' + detail + '.'}
"""
        if self.hallucinate:
            memo += "\nThe business also holds stock worth roughly £48,250 according to the owner.\n"
        return memo


def _require_unlocked_now(what: str, firm_config: "firm.FirmConfig | None") -> None:
    """A hosted client is built only on a reading of firm.yaml with the lock off that load()
    made: the run's own, when it hands it in, or one read now. A FirmConfig the caller built
    can only refuse, never stand in for a reading (R29, T17 review 4)."""
    if firm.claim_for_client(firm_config):  # a fresh reading, for this folder, used once
        firm_config.require_offline_off(what)
        return
    firm.load().require_offline_off(what)
    if firm_config is not None:
        firm_config.require_offline_off(what)


# Set while `complete` sends the one request its lock check allowed: that request is decided
# by the run's own reading, never by firm.yaml read again mid-run (T17 review 14).
_CHECKED_REQUEST: ContextVar[bool] = ContextVar("cma_checked_request", default=False)


def _checked_http(client, what: str) -> None:
    """The SDK client's own HTTP client follows no redirect and checks every HTTP request it
    sends (R29, T17 review 13): the one request a `complete` call's lock check allowed goes,
    decided by the run's reading (T17 review 14); any other, such as a request made through
    the client directly, reads firm.yaml and is refused under the lock. A stand-in client
    without one is left as it is."""
    http = getattr(client, "_client", None)
    if http is None or not hasattr(http, "event_hooks"):
        return
    http.follow_redirects = False

    def check(request) -> None:
        if _CHECKED_REQUEST.get():
            _CHECKED_REQUEST.set(False)  # one request per check: nothing past it rides on it
            return
        firm.require_unlocked(what)

    hooks = dict(http.event_hooks)
    hooks["request"] = [*hooks.get("request", []), check]
    http.event_hooks = hooks


def _lock_error_in(e: BaseException) -> "firm.OfflineLockError | None":
    """The lock refusal behind an SDK error: the SDK wraps what its HTTP client raised."""
    seen = set()
    while e is not None and id(e) not in seen:
        if isinstance(e, firm.OfflineLockError):
            return e
        seen.add(id(e))
        e = e.__cause__ or e.__context__
    return None


class HostedLLM:
    """A model that runs off this machine. Every way it can send data checks the offline
    lock first: making the client, and every request.

    The SDK client is built with its own retries off (max_retries=0), so each request the
    SDK sends is one `complete` call, and the lock check before it covers it. A run passes
    the lock value it read from firm.yaml at its start (`offline_lock`), so the value that
    decides a request is the one the run's evidence records; a caller outside a run passes
    nothing, and firm.yaml is read for that request."""

    name = "hosted"

    def _check(self, what: str, offline_lock: bool | None, reading: "firm.FirmConfig | None") -> None:
        # Never on the caller's word (R29, T17 review 4): a request is allowed only by a
        # reading of firm.yaml that load() made, the run's own when it hands it in, or one read
        # now. A bare offline_lock value, or a FirmConfig the caller built, can only refuse.
        if firm.allows_request(reading):  # the run's own reading, while its run is going
            reading.require_offline_off(what)
        else:
            firm.require_unlocked(what)
            if reading is not None:
                reading.require_offline_off(what)
        if offline_lock is not None:
            firm.refuse_if_locked(offline_lock is not False, what)

    def complete(self, system: str, user: str, *, offline_lock: bool | None = None,
                 reading: "firm.FirmConfig | None" = None) -> str:
        self._check(f"A request to a hosted model ({self.name})", offline_lock, reading)
        allowed = _CHECKED_REQUEST.set(True)
        try:
            return self._send(system, user)
        except Exception as e:
            refused = _lock_error_in(e)
            if refused is not None and refused is not e:
                raise refused from None  # the lock's own refusal, not the SDK's wrapping of it
            raise
        finally:
            _CHECKED_REQUEST.reset(allowed)

    def _send(self, system: str, user: str) -> str:
        raise NotImplementedError


class AnthropicLLM(HostedLLM):
    name = "anthropic"

    def __init__(self, model: str | None = None, *, firm_config: firm.FirmConfig | None = None):
        _require_unlocked_now("A hosted model (anthropic)", firm_config)
        import anthropic

        self.client = anthropic.Anthropic(timeout=120.0, max_retries=0)
        _checked_http(self.client, "A request to a hosted model (anthropic)")
        self.model = model or os.getenv("CMA_ANTHROPIC_MODEL", "claude-sonnet-5-5")

    def _send(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if msg.stop_reason == "max_tokens":
            raise LLMError("the model's reply was cut off at the token limit")
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


class OpenAILLM(HostedLLM):
    name = "openai"

    def __init__(self, model: str | None = None, *, firm_config: firm.FirmConfig | None = None):
        _require_unlocked_now("A hosted model (openai)", firm_config)
        from openai import OpenAI

        self.client = OpenAI(timeout=120.0, max_retries=0)
        _checked_http(self.client, "A request to a hosted model (openai)")
        self.model = model or os.getenv("CMA_OPENAI_MODEL", "gpt-4.1")

    def _send(self, system: str, user: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if r.choices[0].finish_reason == "length":
            raise LLMError("the model's reply was cut off at the token limit")
        return r.choices[0].message.content or ""


def get_llm(name: str | None = None, *, firm_config: firm.FirmConfig | None = None) -> LLM:
    """`firm_config` is the run's one reading of firm.yaml; without it firm.yaml is read here."""
    name = (name or os.getenv("CMA_LLM", "fake")).lower()
    if name == "fake":
        return FakeLLM()
    if name == "anthropic":
        return AnthropicLLM(firm_config=firm_config)
    if name == "openai":
        return OpenAILLM(firm_config=firm_config)
    raise ValueError(f"unknown LLM provider {name!r}")
