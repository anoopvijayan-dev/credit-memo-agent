"""Model providers behind one tiny interface.

The memo graph only needs `complete(system, user) -> str`. `FakeLLM` writes a memo
from the metrics table with no network call, so tests and CI run without keys. It
also has a `hallucinate` switch so the grounding guard can be tested against a memo
that invents a number.
"""

from __future__ import annotations

import os
import re
from typing import Protocol


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
        overall = re.search(r"Overall policy outcome: (\w+)", user)
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


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic(timeout=120.0, max_retries=2)
        self.model = model or os.getenv("CMA_ANTHROPIC_MODEL", "claude-sonnet-5-5")

    def complete(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if msg.stop_reason == "max_tokens":
            raise LLMError("the model's reply was cut off at the token limit")
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


class OpenAILLM:
    name = "openai"

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.client = OpenAI(timeout=120.0, max_retries=2)
        self.model = model or os.getenv("CMA_OPENAI_MODEL", "gpt-4.1")

    def complete(self, system: str, user: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if r.choices[0].finish_reason == "length":
            raise LLMError("the model's reply was cut off at the token limit")
        return r.choices[0].message.content or ""


def get_llm(name: str | None = None) -> LLM:
    name = (name or os.getenv("CMA_LLM", "fake")).lower()
    if name == "fake":
        return FakeLLM()
    if name == "anthropic":
        return AnthropicLLM()
    if name == "openai":
        return OpenAILLM()
    raise ValueError(f"unknown LLM provider {name!r}")
