"""Firm configuration: firm.yaml at the case root.

    offline_lock: true          # default; no hosted model, no MCP server
    show_policy_verdict: true   # default; show the illustrative pass/refer/fail
    paths:                      # optional; relative paths are under the case root
      firm_rules: firm_rules.yaml
      thresholds: indicators.yaml
      memo_fields: memo_fields.yaml

The case root is CMA_HOME, or private/cma under the working directory. A missing file
means the defaults. A malformed file is refused, never read as something else: a lock
that silently falls back is no lock.

The offline lock is checked before a hosted model client is created and before the MCP
server starts, so under the lock neither gets as far as a network call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

FILE_NAME = "firm.yaml"
FLAGS = ("offline_lock", "show_policy_verdict")
DEFAULT_PATHS = {"firm_rules": "firm_rules.yaml", "thresholds": "indicators.yaml", "memo_fields": "memo_fields.yaml"}


class FirmConfigError(ValueError):
    """firm.yaml is malformed; nothing should run under a setting nobody chose."""


class OfflineLockError(RuntimeError):
    """Something that would send data off this machine was asked for under the offline lock."""


@dataclass(frozen=True)
class FirmConfig:
    root: Path
    offline_lock: bool = True
    show_policy_verdict: bool = True
    paths: tuple[tuple[str, str], ...] = tuple(DEFAULT_PATHS.items())

    def _path(self, key: str) -> Path:
        return self.root / dict(self.paths)[key]  # an absolute path replaces the root

    @property
    def firm_rules_path(self) -> Path:
        return self._path("firm_rules")

    @property
    def thresholds_path(self) -> Path:
        return self._path("thresholds")

    @property
    def memo_fields_path(self) -> Path:
        return self._path("memo_fields")

    def require_offline_off(self, what: str) -> None:
        if self.offline_lock:
            raise OfflineLockError(
                f"{what} refused: the offline lock is on in {self.root / FILE_NAME}, so no data leaves this "
                "machine. Set offline_lock: false there to allow it."
            )


def case_root() -> Path:
    raw = os.environ.get("CMA_HOME")
    return Path(raw).resolve() if raw else (Path.cwd() / "private" / "cma").resolve()


def load(root: Path | None = None) -> FirmConfig:
    root = root or case_root()
    path = root / FILE_NAME
    if not path.is_file():
        return FirmConfig(root=root)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise FirmConfigError(f"{path}: not valid YAML ({e.__class__.__name__})") from e
    if data is None:
        return FirmConfig(root=root)
    if not isinstance(data, dict):
        raise FirmConfigError(f"{path}: not a mapping")

    problems: list[str] = []
    problems += [f"unknown key {k!r}" for k in data if k not in (*FLAGS, "paths")]
    flags = {}
    for key in FLAGS:
        if key in data:
            if isinstance(data[key], bool):
                flags[key] = data[key]
            else:
                problems.append(f"{key} must be true or false")
    paths = dict(DEFAULT_PATHS)
    raw_paths = data.get("paths", {}) or {}
    if not isinstance(raw_paths, dict):
        problems.append("paths must be a mapping")
        raw_paths = {}
    for key, value in raw_paths.items():
        if key not in DEFAULT_PATHS:
            problems.append(f"unknown path {key!r}")
        elif not isinstance(value, str) or not value.strip():
            problems.append(f"paths.{key} must be a file path")
        else:
            paths[key] = value
    if problems:
        raise FirmConfigError(f"{path}: " + "; ".join(problems))
    return FirmConfig(root=root, paths=tuple(paths.items()), **flags)
