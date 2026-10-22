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


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class FakeLLM:
    name = "fake"

    def __init__(self, hallucinate: bool = False):
        self.hallucinate = hallucinate

    def complete(self, system: str, user: str) -> str:
        # Pull the metric table rows out of the prompt and write a plain memo from them.
        rows = re.findall(r"\| (M\d+) \| ([^|]+) \| ([^|]+) \|", user)
        by_id = {mid: (name.strip(), val.strip()) for mid, name, val in rows}
        overall = re.search(r"Overall policy outcome: (\w+)", user)
        outcome = overall.group(1) if overall else "refer"

        def q(mid: str) -> str:
            _, val = by_id[mid]
            return f"{val} [{mid}]"

        memo = f"""## Business overview
The statements cover {q('M1')} months of trading. Total revenue over the period was {q('M2')}, averaging {q('M3')} a month.

## Revenue
Revenue moved {q('M4')} comparing the last three months with the first three. The largest single payer accounted for {q('M15')} of inflows.

## Cash position
The lowest balance reached was {q('M5')}, and the account was overdrawn on {q('M6')} days.

## Obligations and conduct
Loan repayments totalled {q('M8')} against net operating cash flow of {q('M9')}, giving debt service cover of {q('M10')}. Returned direct debits in the period: {q('M7')}. VAT paid was {q('M12')} with {q('M13')} late payments; PAYE paid was {q('M14')}. Owner drawings were {q('M11')}.

## Recommendation
Policy outcome: {outcome}. {'The case meets policy and can proceed to approval.' if outcome == 'pass' else 'The case needs an analyst decision on the referred rules before any offer.'}
"""
        if self.hallucinate:
            memo += "\nThe business also holds stock worth roughly £48,250 according to the owner.\n"
        return memo


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.getenv("CMA_ANTHROPIC_MODEL", "claude-sonnet-4-5")

    def complete(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


class OpenAILLM:
    name = "openai"

    def __init__(self, model: str | None = None):
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model or os.getenv("CMA_OPENAI_MODEL", "gpt-4.1")

    def complete(self, system: str, user: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
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
