"""Firm configuration: firm.yaml at the case root.

    offline_lock: true          # default; no hosted model, no MCP server
    show_policy_verdict: true   # default; show the illustrative pass/refer/fail
    paths:                      # optional; relative paths are under the case root
      firm_rules: firm_rules.yaml
      thresholds: indicators.yaml
      memo_fields: memo_fields.yaml

The case root is CMA_HOME, or private/cma under the working directory. A missing file
means the defaults. A malformed file (not UTF-8, not YAML, a key given twice, an unknown
key or a value of the wrong kind) is refused, never read as something else: a lock
that silently falls back is no lock.

The offline lock is checked before a hosted model client is created and before the MCP
server starts, so under the lock neither gets as far as a network call.
"""

from __future__ import annotations

import hashlib
import os
import weakref
from dataclasses import dataclass
from pathlib import Path

import yaml

FILE_NAME = "firm.yaml"
FLAGS = ("offline_lock", "show_policy_verdict")
DEFAULT_PATHS = {"firm_rules": "firm_rules.yaml", "thresholds": "indicators.yaml", "memo_fields": "memo_fields.yaml"}


class FirmConfigError(ValueError):
    """firm.yaml is malformed; nothing should run under a setting nobody chose."""


class _RepeatedKey(Exception):
    def __init__(self, key):
        self.key = key


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load, except that a key given twice is refused: YAML would otherwise keep the
    last value, so `offline_lock: true` then `offline_lock: false` would silently unlock."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                hashable = key in seen
            except TypeError:
                raise yaml.constructor.ConstructorError(None, None, "unhashable key", key_node.start_mark) from None
            if hashable:
                raise _RepeatedKey(key)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class OfflineLockError(RuntimeError):
    """Something that would send data off this machine was asked for under the offline lock."""


@dataclass(frozen=True)
class FirmConfig:
    root: Path
    offline_lock: bool = True
    show_policy_verdict: bool = True
    paths: tuple[tuple[str, str], ...] = tuple(DEFAULT_PATHS.items())
    named: tuple[str, ...] = ()  # the paths firm.yaml names itself, not left at their defaults

    def _path(self, key: str) -> Path:
        return self.root / dict(self.paths)[key]  # an absolute path replaces the root

    @property
    def firm_rules_path(self) -> Path:
        return self._path("firm_rules")

    @property
    def thresholds_path(self) -> Path:
        return self._path("thresholds")

    def names_path(self, key: str) -> bool:
        """Whether firm.yaml itself names the file for `key`: the firm chose that file, so a
        missing one is refused, never replaced by the packaged default."""
        return key in self.named

    @property
    def memo_fields_path(self) -> Path:
        return self._path("memo_fields")

    def require_offline_off(self, what: str) -> None:
        _refuse(self.offline_lock, what, self.root)


def _refuse(offline_lock: bool, what: str, root: Path) -> None:
    if offline_lock is not False:  # anything but an explicit False is the lock
        raise OfflineLockError(
            f"{what} refused: the offline lock is on in {root / FILE_NAME}, so no data leaves this "
            "machine. Set offline_lock: false there to allow it."
        )


def refuse_if_locked(offline_lock: bool, what: str) -> None:
    """Refuse `what` if `offline_lock`, a value already read from firm.yaml (a run's one
    reading), is on. The same value is what the run's evidence records."""
    _refuse(offline_lock, what, case_root())


def require_unlocked(what: str) -> None:
    """Refuse `what` unless firm.yaml, read now, has the offline lock off.

    Read on every call, not once: a client or server made while the lock was off must
    stop sending as soon as the firm turns it back on."""
    load().require_offline_off(what)


def case_root() -> Path:
    raw = os.environ.get("CMA_HOME")
    return Path(raw).resolve() if raw else (Path.cwd() / "private" / "cma").resolve()


# Every reading load() makes, by identity: a hosted client or request accepts only one of
# these as a run's reading, never a FirmConfig a caller made (R29, T17 review 4).
_READINGS: dict[int, weakref.ref] = {}


def is_reading(config: object) -> bool:
    """Whether `config` is a reading load() made, not a FirmConfig made by its caller."""
    ref = _READINGS.get(id(config))
    return ref is not None and ref() is config


# Readings driving a run now, and readings that have already authorised a client: a reading
# is bound to its run and its firm folder, never reused as an allowance (T17 review 5).
_IN_RUN: dict[int, int] = {}
_CLAIMED: set[int] = set()
_RAN: set[int] = set()  # readings that have driven a run: each drives one (T17 review 6)
_STAMP: dict[int, tuple] = {}  # firm.yaml as it stood when each reading was made


def _snapshot(root: Path) -> tuple[tuple, bytes | None]:
    """firm.yaml as it stands, read once: its stamp (size, change time and a hash of the bytes
    read, or that it is absent) and those bytes. A reading is parsed from the very bytes its
    stamp hashes, so a file changed between two reads can never pair one version's settings
    with another's stamp (T17 review 15)."""
    path = root / FILE_NAME
    try:
        st = path.stat()
        data = path.read_bytes()
    except OSError:
        return ("absent",), None
    return (st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest()), data


def _stamp(root: Path) -> tuple:
    """firm.yaml's stamp as it stands now: any change since a reading makes that reading
    stale, even a file of the same size and time (T17 reviews 6 and 7)."""
    return _snapshot(root)[0]


def unchanged_since(reading: FirmConfig) -> bool:
    """Whether firm.yaml is as it was when load() made `reading` (T17 review 6)."""
    return _here(reading) and _STAMP.get(id(reading)) == _stamp(reading.root)


def take_for_run(reading: object) -> bool:
    """Whether `reading` may be a run's reading: a reading load() made, for the folder in use,
    that has driven no run before (T17 review 6), while firm.yaml is unchanged since it was
    made, so a lock turned on before the run starts is the run's lock (T17 review 7)."""
    if not (isinstance(reading, FirmConfig) and unchanged_since(reading)) or id(reading) in _RAN:
        return False
    _RAN.add(id(reading))
    return True


def _here(config: FirmConfig) -> bool:
    return is_reading(config) and config.root == case_root()


# Each going run's key, by its reading: a run's reading allows only the requests its own run
# makes, which carry the key from the run's config, never those of another caller holding the
# same reading, on another thread or the same one while a stream is suspended (T17 reviews 16
# and 17). The key is an object only the run's config holds.
_RUN_KEYS: dict[int, set] = {}


def begin_run(reading: FirmConfig) -> object:
    """Marks `reading`'s run as going; returns the run's key, which its requests carry."""
    _IN_RUN[id(reading)] = _IN_RUN.get(id(reading), 0) + 1
    key = object()
    _RUN_KEYS.setdefault(id(reading), set()).add(key)
    return key


def end_run(reading: FirmConfig, key: object = None) -> None:
    left = _IN_RUN.get(id(reading), 0) - 1
    if left > 0:
        _IN_RUN[id(reading)] = left
    else:
        _IN_RUN.pop(id(reading), None)
    keys = _RUN_KEYS.get(id(reading))
    if keys is not None:
        keys.discard(key)
        if not keys:
            _RUN_KEYS.pop(id(reading), None)


def allows_request(reading: object, key: object = None) -> bool:
    """Whether `reading` may decide a hosted request: a reading load() made, for the firm folder
    in use, whose run is still going, asked by that run itself with its key (T17 reviews 16
    and 17). Any other is no allowance; firm.yaml is read instead."""
    return (isinstance(reading, FirmConfig) and _here(reading) and id(reading) in _IN_RUN
            and key is not None and key in _RUN_KEYS.get(id(reading), ()))


def claim_for_client(reading: object) -> bool:
    """Whether `reading` may decide building one hosted client: a reading load() made, for the
    firm folder in use, that has not already decided one, while firm.yaml is unchanged since
    it was made (T17 review 6)."""
    if not (isinstance(reading, FirmConfig) and unchanged_since(reading)) or id(reading) in _CLAIMED:
        return False
    _CLAIMED.add(id(reading))
    return True


def load(root: Path | None = None) -> FirmConfig:
    """firm.yaml, read now, once: the settings are parsed from the bytes the stamp hashes."""
    stamp, data = _snapshot(root or case_root())
    config = _load(root, data)
    key = id(config)
    _STAMP[key] = stamp
    _READINGS[key] = weakref.ref(config, lambda _ref, k=key: (_READINGS.pop(k, None), _CLAIMED.discard(k),
                                                              _IN_RUN.pop(k, None), _RAN.discard(k),
                                                              _RUN_KEYS.pop(k, None),
                                                              _STAMP.pop(k, None)))
    return config


def _load(root: Path | None = None, data: bytes | None = None) -> FirmConfig:
    """`data`: firm.yaml's bytes as load() read them; None when it could not read them."""
    root = root or case_root()
    path = root / FILE_NAME
    if data is None:
        if not path.is_file():
            return FirmConfig(root=root)
        raise FirmConfigError(f"{path}: cannot be read as UTF-8 text (OSError)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise FirmConfigError(f"{path}: cannot be read as UTF-8 text ({e.__class__.__name__})") from e
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as e:
        raise FirmConfigError(f"{path}: not valid YAML ({e.__class__.__name__})") from e
    except _RepeatedKey as e:
        raise FirmConfigError(f"{path}: key {e.key!r} is given more than once") from None
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
    named: list[str] = []
    raw_paths = data.get("paths")
    if raw_paths is None:  # absent, or `paths:` with nothing under it
        raw_paths = {}
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
            named.append(key)
    if problems:
        raise FirmConfigError(f"{path}: " + "; ".join(problems))
    return FirmConfig(root=root, paths=tuple(paths.items()), named=tuple(named), **flags)
