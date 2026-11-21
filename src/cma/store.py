"""The case store (C3, R31): the case root, case folders, the case profile and imports.

    <case root>/                 CMA_HOME, or private/cma under the working directory
      cases/<random id>/
        case.yaml                the profile (validated; see PROFILE_FIELDS)
        inputs/<sha256>.<ext>    each imported file as it arrived, read-only, named by hash
        statements/<account>.csv the five-column statement the loader reads, one per account
        log.jsonl (+ .head, .gate, .lock)   the case log (case.py)
      purge_ledger.jsonl         one line per purged case: its id, the time, who (R35)

The root and every folder under it are readable only by the operating-system user who
made them (0700), and every file this module writes is 0600 (inputs 0400), so a case's
statements, profile, logs and outputs never sit where another user can read them (R31).
A root inside a git working tree that does not ignore it is refused before anything is
made, because a case committed by mistake would publish an applicant's bank data (R31).
A root inside a folder a known cloud-sync client uploads (OneDrive, Dropbox, Google
Drive, iCloud Drive) is not refused, but `cma case new` warns before making the case and
records the warning as the case log's second entry (R32).

Every change to a case (its creation, a profile change, an import) is appended to the case
log while this process holds the case (R42), after the files are in place; if the log
refuses the entry, the files are put back as they were, so the log and the folder agree.
Messages name files by hash or account key, never by their content.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePath
from typing import Any

import yaml

from . import case as caselog
from . import firm

CASES = "cases"
PURGE_LEDGER = "purge_ledger.jsonl"
PURGING = ".purging-"  # a case folder renamed out of use, while its files are deleted
PROFILE = "case.yaml"
INPUTS = "inputs"
STATEMENTS = "statements"
FORMATS = ("csv", "monzo")
# Matched with fullmatch: `$` alone would also take a key ending in a newline (T13 review 2).
CASE_ID = re.compile(r"[0-9a-f]{16}")  # random; never the applicant's name
ACCOUNT_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
DIR_MODE, FILE_MODE, INPUT_MODE = 0o700, 0o600, 0o400
LIMIT_PENCE = 10_000_000_000_000 * 100  # R2's limit, for the proposed repayment too

# The profile (case.yaml). Every key is optional in the file; a missing key is its default.
PROFILE_FIELDS: dict[str, Any] = {
    "label": None,                    # the underwriter's own label for the case
    "demo": False,                    # synthetic data or the owner's own statements (R48, D23)
    "business_name": None,
    "directors": [],                  # directors' names (R5)
    "shareholders": [],               # shareholders' names (R5)
    "own_accounts": [],               # the applicant's other accounts (R6)
    "vat_stagger": None,              # HMRC stagger group 1, 2 or 3
    "proposed_monthly_repayment": None,  # GBP text with at most two decimals (R13)
}
LIST_FIELDS = ("directors", "shareholders", "own_accounts")
TEXT_FIELDS = ("label", "business_name")


class CaseStoreError(ValueError):
    """The case root, a case folder, the profile or an import is refused; nothing changed."""


class CaseRootInGit(CaseStoreError):
    """The case root lies inside a git working tree that does not ignore it (R31)."""


# --- the case root ---------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    # The repository is found from the path, never from a GIT_DIR the caller's shell set.
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _enclosing_repo(path: Path) -> Path | None:
    """The nearest folder at or above `path` that holds a .git, or None."""
    for folder in (path, *path.parents):
        if (folder / ".git").exists():
            return folder
    return None


def check_not_in_git(root: Path) -> None:
    """Refuse a case root inside a git working tree unless git ignores it (R31). A tree git
    cannot be asked about (no git installed, an error) is refused too: unknown is not
    ignored."""
    root = Path(root).resolve()
    repo = _enclosing_repo(root)
    if repo is None:
        return
    rel = root.relative_to(repo).as_posix()
    if rel == ".":
        raise CaseRootInGit(f"case root {root} is a git working tree itself; choose a folder git ignores, "
                            "or set CMA_HOME outside the repository")
    try:
        # A trailing slash asks about the folder, so a folder-only pattern (cma/) matches a
        # root not made yet.
        done = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--", rel + "/"],
                              env=_git_env(), capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise CaseRootInGit(f"case root {root} is inside the git working tree {repo}, and git could not "
                            f"say whether it is ignored ({type(e).__name__}); refused") from None
    if done.returncode == 0:
        return
    if done.returncode == 1:
        raise CaseRootInGit(f"case root {root} is inside the git working tree {repo} and git does not "
                            f"ignore it: add {rel}/ to .gitignore, or set CMA_HOME outside the repository")
    raise CaseRootInGit(f"case root {root} is inside the git working tree {repo}, and git could not say "
                        "whether it is ignored; refused")


# --- cloud-sync folders (R32) ----------------------------------------------------------

# Windows sets these to the folder each signed-in OneDrive account syncs, whatever its name.
ONEDRIVE_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")


class SyncFolderWarning(UserWarning):
    """The case root lies inside a folder a known cloud-sync client uploads (R32)."""


@dataclass(frozen=True)
class SyncFolder:
    client: str         # OneDrive, Dropbox, Google Drive or iCloud Drive
    folder: PurePath    # the synced folder that holds the path


def _client_named(name: str, before: str | None) -> str | None:
    """The client whose synced folder is named `name` (one path component), as each client
    lays it out on Windows, macOS (~/Library/CloudStorage, ~/Library/Mobile Documents) and
    Linux; `before` is the component above it. None for any other name."""
    n = name.casefold()
    if n == "onedrive" or n.startswith(("onedrive - ", "onedrive-")):
        return "OneDrive"
    if n == "dropbox" or n.startswith("dropbox-") or re.fullmatch(r"dropbox \(.+\)", n):
        return "Dropbox"
    if n in ("google drive", "googledrive", "my drive") or n.startswith("googledrive-"):
        return "Google Drive"
    if n in ("iclouddrive", "icloud drive", "com~apple~clouddocs") or (
            n == "mobile documents" and (before or "").casefold() == "library"):
        return "iCloud Drive"
    return None


def _dropbox_roots(home: Path, env: dict[str, str]) -> list[Path]:
    """The folders Dropbox's own info file says it syncs, which may have any name."""
    places = [home / ".dropbox" / "info.json"]
    places += [Path(env[v]) / "Dropbox" / "info.json" for v in ("APPDATA", "LOCALAPPDATA") if env.get(v)]
    roots = []
    for place in places:
        try:
            info = json.loads(place.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # no file, or one we cannot read: it names nothing
        if isinstance(info, dict):
            roots += [Path(a["path"]) for a in info.values()
                      if isinstance(a, dict) and isinstance(a.get("path"), str) and a["path"]]
    return roots


def _within(path: PurePath, root: PurePath) -> bool:
    a = [p.casefold() for p in path.parts]
    b = [p.casefold() for p in root.parts]
    return len(b) > 0 and a[:len(b)] == b


def sync_folder(path: PurePath, *, home: Path | None = None,
                env: dict[str, str] | None = None) -> SyncFolder | None:
    """The known cloud-sync folder that holds `path`, or None (R32). A folder is known by
    its client's own name for it, by the OneDrive variables Windows sets, or by Dropbox's
    info file; a symlink is followed, since the files land where it points. This is a
    warning, not a lock: a folder synced under a name no client uses is not seen."""
    env = dict(os.environ) if env is None else env
    home = Path.home() if home is None else home
    paths = [path]
    if isinstance(path, Path):
        with suppress(OSError, RuntimeError):
            resolved = path.resolve()
            if resolved != path:
                paths.append(resolved)
    roots = [("OneDrive", PurePath(env[v])) for v in ONEDRIVE_VARS
             if env.get(v) and PurePath(env[v]).is_absolute()]
    roots += [("Dropbox", r) for r in _dropbox_roots(home, env) if r.is_absolute()]
    for p in paths:
        for client, root in roots:
            if _within(p, root):
                return SyncFolder(client, type(p)(root))
        parts = p.parts
        for i, part in enumerate(parts):
            client = _client_named(part, parts[i - 1] if i else None)
            if client:
                return SyncFolder(client, type(p)(*parts[: i + 1]))
    return None


def sync_warning(root: Path, found: SyncFolder) -> str:
    return (f"the case root {root} lies inside {found.client}'s folder ({found.folder}). "
            f"{found.client} uploads what that folder holds, so an applicant's statements and "
            f"profile would leave this machine. Keep cases in a local folder no sync client "
            f"manages (set CMA_HOME). This warning is recorded in the case log.")


def _private_dir(path: Path) -> None:
    """Make `path` (one level) owner-only, or make an existing one so. A folder another user
    owns is refused: this user cannot keep it private."""
    try:
        path.mkdir(mode=DIR_MODE)
    except FileExistsError:
        if not path.is_dir() or path.is_symlink():
            raise CaseStoreError(f"{path} is not a folder") from None
    st = path.stat()
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise CaseStoreError(f"{path} belongs to another user; a case folder must be this user's own")
    if st.st_mode & 0o777 != DIR_MODE:
        os.chmod(path, DIR_MODE)  # mkdir's mode is narrowed by the umask, never widened; set it


def ensure_root(root: Path | None = None) -> Path:
    """The case root, made owner-only, with its cases folder. Checked against git first, so
    nothing is made inside a tree that does not ignore it."""
    root = Path(root).resolve() if root is not None else firm.case_root()
    check_not_in_git(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    _private_dir(root)
    _private_dir(root / CASES)
    return root


def case_folder(case_id: str, root: Path | None = None) -> Path:
    """The folder of an existing case, after checking the root as ensure_root does."""
    if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
        raise CaseStoreError("a case id is 16 lowercase hex characters, as `cma case new` prints it")
    root = ensure_root(root)
    folder = root / CASES / case_id
    if not (folder / PROFILE).is_file():
        raise CaseStoreError(f"no case {case_id} under {root}")
    return folder


# --- files -----------------------------------------------------------------------------


def _write_private(target: Path, data: bytes, mode: int = FILE_MODE) -> None:
    """Write `data` beside `target` and rename it into place: a reader sees the old file or
    the new one, never part of one, and the file is never readable by another user."""
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(name)
        raise


@dataclass
class _Save:
    """What a body under _recorded hands back: the entry to append (None appends nothing),
    and the steps that put the folder back if the entry is not recorded."""

    entries: list
    data: dict[str, Any] | None = None
    undo: list = field(default_factory=list)


@contextmanager
def _recorded(folder: Path, event: str) -> Iterator[_Save]:
    """Hold the case, check its log whole, let the body read the case and change files, then
    append `event` with the data the body set. Everything the body reads, it reads while
    the case is held, so a change it bases on the case cannot be based on a stale read
    (T13 review 2). If the log refuses the entry (or the body fails), the undo steps run,
    so the folder is as before. A save the log could not confirm keeps its files: its entry
    may be in the log (R42), and `cma case check` says."""
    log = caselog.for_case(folder)
    with log.writer():
        head = log.require_intact()
        save = _Save(entries=log.entries())
        try:
            yield save
            if save.data is not None:
                log.append(event, save.data, expected=head.position)
        except caselog.SaveNotConfirmed:
            raise
        except BaseException:
            for step in reversed(save.undo):
                with suppress(OSError):
                    step()
            raise


def _restorer(path: Path, mode: int = FILE_MODE):
    """An undo step that puts `path` back as it is now: its bytes, or no file."""
    before = path.read_bytes() if path.is_file() else None
    if before is None:
        return lambda: path.unlink(missing_ok=True)
    return lambda: _write_private(path, before, mode)


# --- the profile -----------------------------------------------------------------------


class _Loader(firm._UniqueKeyLoader):
    """safe_load with a key given twice refused (firm.yaml's rule)."""


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise CaseStoreError(f"{where} must be text")
    value = value.strip()
    # A value of only spaces, dashes or dots names no one and would match everywhere (D72).
    if not any(ch.isalnum() for ch in value):
        raise CaseStoreError(f"{where} names no one: it has no letter or digit")
    if len(value) > 200:
        raise CaseStoreError(f"{where} is longer than 200 characters")
    return value


def _repayment(value: Any) -> str:
    where = "proposed_monthly_repayment"
    if isinstance(value, bool):
        raise CaseStoreError(f"{where} must be an amount in pounds, such as 1500.00")
    if isinstance(value, float):
        value = repr(value)  # YAML read 1500.50 as a float; its shortest form is the text typed
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        raise CaseStoreError(f"{where} must be an amount in pounds, such as 1500.00")
    text = value.strip().replace(",", "").removeprefix("£")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise CaseStoreError(f"{where} must be an amount in pounds, such as 1500.00") from None
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        raise CaseStoreError(f"{where} must be pounds with at most two decimals")
    if amount <= 0:
        raise CaseStoreError(f"{where} must be more than £0.00")
    if amount * 100 >= LIMIT_PENCE:
        raise CaseStoreError(f"{where} reaches £10,000,000,000,000; no figure could be reported to the penny")
    return f"{amount:.2f}"


def validate_profile(raw: Any) -> dict[str, Any]:
    """The profile a case.yaml holds, every field checked; CaseStoreError naming the field
    otherwise. Unknown keys are refused, never ignored: a misspelt key would drop a director."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise CaseStoreError("the profile must be a mapping of field: value")
    unknown = sorted(str(k) for k in raw if k not in PROFILE_FIELDS)
    if unknown:
        raise CaseStoreError(f"unknown profile field(s): {', '.join(unknown)}; "
                             f"known fields are {', '.join(PROFILE_FIELDS)}")
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in PROFILE_FIELDS.items()}
    for key, value in raw.items():
        if value is None:
            if key in LIST_FIELDS:
                out[key] = []
            elif key == "demo":
                raise CaseStoreError("demo must be true or false")
            continue
        if key in TEXT_FIELDS:
            out[key] = _name(value, key)
        elif key == "demo":
            if not isinstance(value, bool):
                raise CaseStoreError("demo must be true or false")
            out[key] = value
        elif key in LIST_FIELDS:
            if not isinstance(value, list):
                raise CaseStoreError(f"{key} must be a list of names")
            names = [_name(v, f"{key} item {i}") for i, v in enumerate(value, 1)]
            if len({n.upper() for n in names}) != len(names):
                raise CaseStoreError(f"{key} names someone twice")
            out[key] = names
        elif key == "vat_stagger":
            if type(value) is not int or value not in (1, 2, 3):  # not 1.0, not true
                raise CaseStoreError("vat_stagger must be 1, 2 or 3")
            out[key] = value
        elif key == "proposed_monthly_repayment":
            out[key] = _repayment(value)
    return out


def _profile_bytes(profile: dict[str, Any]) -> bytes:
    return yaml.safe_dump(profile, sort_keys=False, allow_unicode=True).encode("utf-8")


def profile_sha256(profile: dict[str, Any]) -> str:
    """What evidence and the log record of a profile: its hash, never its names."""
    return hashlib.sha256(_profile_bytes(validate_profile(profile))).hexdigest()


def parse_profile(data: bytes, where: str = PROFILE) -> dict[str, Any]:
    try:
        raw = yaml.load(data.decode("utf-8"), Loader=_Loader)  # _Loader is a SafeLoader
    except UnicodeDecodeError:
        raise CaseStoreError(f"{where} is not UTF-8 text") from None
    except firm._RepeatedKey as e:
        raise CaseStoreError(f"{where}: the key {e.key!r} is given more than once; keep one") from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        at = f" (line {mark.line + 1})" if mark is not None else ""
        raise CaseStoreError(f"{where} is not valid YAML{at}") from None
    try:
        return validate_profile(raw)
    except CaseStoreError as e:
        raise CaseStoreError(f"{where}: {e}") from None


def load_profile(folder: Path) -> dict[str, Any]:
    return parse_profile((Path(folder) / PROFILE).read_bytes(), str(Path(folder) / PROFILE))


def profile_names(profile: dict[str, Any]):
    """The names the categoriser matches (R5, R6)."""
    from .categorise import ProfileNames

    return ProfileNames(directors=tuple(profile["directors"]) + tuple(profile["shareholders"]),
                        own_accounts=tuple(profile["own_accounts"]))


# --- commands --------------------------------------------------------------------------


def new_case(*, label: str | None = None, demo: bool = False, root: Path | None = None,
             warn: Callable[[str], None] | None = None) -> str:
    """Make a case folder with a random id, its profile and its first log entry.

    If the case root lies inside a folder a known cloud-sync client uploads, the warning is
    given before anything of the case is made, through `warn` (a Python SyncFolderWarning
    when none is given), and is recorded as the case log's second entry (R32)."""
    profile = validate_profile({"label": label, "demo": demo})
    # Warned before anything is made, the case root and its cases folder included: on a first
    # run ensure_root would make them inside the synced folder before the warning (R32). A
    # root git would publish is refused first, so no warning precedes a refusal.
    target = Path(root).resolve() if root is not None else firm.case_root()
    check_not_in_git(target)
    found = sync_folder(target)
    message = sync_warning(target, found) if found else None
    if message:
        if warn is None:
            warnings.warn(message, SyncFolderWarning, stacklevel=2)
        else:
            warn(message)
    root = ensure_root(target)
    for _ in range(8):
        case_id = secrets.token_hex(8)
        folder = root / CASES / case_id
        try:
            folder.mkdir(mode=DIR_MODE)
        except FileExistsError:
            continue
        break
    else:  # pragma: no cover - 64 random bits colliding eight times
        raise CaseStoreError("could not pick an unused case id")
    try:
        os.chmod(folder, DIR_MODE)
        for sub in (INPUTS, STATEMENTS):
            _private_dir(folder / sub)
        _write_private(folder / PROFILE, _profile_bytes(profile))
        log = caselog.for_case(folder)
        with log.writer():
            log.append("case_created", {"case": case_id, "demo": profile["demo"],
                                        "profile_sha256": profile_sha256(profile)}, expected=0)
            if found:
                log.append("sync_folder_warning", {"client": found.client,
                                                   "sync_folder": str(found.folder),
                                                   "case_root": str(root),
                                                   "warning": message}, expected=1)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # no half-made case is left behind
        raise
    return case_id


def update_profile(folder: Path, update: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    """Change a case's profile and log the change (R42). `update` is given the profile as it
    stands once this process holds the case, and returns the new one, which is checked. So
    two overlapping changes are made one after the other, the second on the first's
    result, never on a profile read before the first was saved (T13 review 2)."""
    folder = Path(folder)
    target = folder / PROFILE
    with _recorded(folder, "profile_changed") as save:
        old = load_profile(folder)
        new = validate_profile(update({k: (list(v) if isinstance(v, list) else v) for k, v in old.items()}))
        if new == old:
            return new  # nothing changed, so nothing is logged
        save.undo.append(_restorer(target))
        _write_private(target, _profile_bytes(new))
        save.data = {"fields": sorted(k for k in PROFILE_FIELDS if new[k] != old[k]),
                     "profile_sha256": profile_sha256(new)}
    return new


def set_profile(folder: Path, profile: dict[str, Any]) -> dict[str, Any]:
    """Replace a case's profile with `profile`, checked, and log the change (R42)."""
    new = validate_profile(profile)
    return update_profile(folder, lambda _old: new)


@dataclass(frozen=True)
class Imported:
    account: str
    input_sha256: str
    lines: int
    start: str
    end: str
    already: bool = False


def import_file(folder: Path, src: Path, fmt: str, *, account: str = "main",
                opening_balance: str | None = None) -> Imported:
    """Import a bank file into a case: the original into inputs/ by its hash, the converted
    statement into statements/<account>.csv, checked by the statement loader, and one log
    entry (R42). The same file imported again leaves the case unchanged (R39)."""
    from .importers import monzo
    from .ingest import StatementError, load_csv

    folder = Path(folder)
    if fmt not in FORMATS:
        raise CaseStoreError(f"unknown format {fmt!r}; use one of {', '.join(FORMATS)}")
    if not isinstance(account, str) or not ACCOUNT_KEY.fullmatch(account):
        raise CaseStoreError("an account key is lowercase letters, digits, _ and -, starting with a "
                             "letter or digit, at most 32 characters")
    src = Path(src)
    if not src.is_file():
        raise CaseStoreError(f"no file at {src}")
    data = src.read_bytes()  # read once; hashed and converted from these bytes
    sha = hashlib.sha256(data).hexdigest()
    # Whether a file was imported is what the case log says, never whether its copy is in
    # inputs/: a copy whose entry never reached the log (a crash between the two, or a save
    # not confirmed whose entry a repair dropped) is a leftover, and importing the file
    # again records it (T13 review 2). A look before the work; the decision is made again
    # below, holding the case.
    with suppress(caselog.CaseLogError):
        if any(_imported(e, sha) for e in caselog.for_case(folder).entries()):
            return Imported(account, sha, 0, "", "", already=True)
    statement = folder / STATEMENTS / f"{account}.csv"

    suffix = "".join(s.lower() for s in src.suffixes[-1:] if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", s))
    inp = folder / INPUTS / f"{sha}{suffix or '.csv'}"
    with tempfile.TemporaryDirectory(dir=folder) as work:
        work = Path(work)
        os.chmod(work, DIR_MODE)
        if fmt == "monzo":
            raw = work / "export.csv"
            converted = work / "statement.csv"
            _write_private(raw, data)
            _write_private(converted, b"")  # made 0600 before the importer writes into it
            try:
                monzo.convert(raw, converted, opening_balance)
            except monzo.MonzoImportError as e:
                raise CaseStoreError(f"import refused: {str(e).replace(str(raw), src.name)}") from None
            out = converted.read_bytes()
        else:
            if opening_balance is not None:
                raise CaseStoreError("--opening-balance is for a Monzo export; a statement CSV has its balances")
            out = data
        try:
            stmt = load_csv(statement, account_name=account, data=out)
        except StatementError as e:
            raise CaseStoreError(f"import refused: {str(e).replace(str(statement), src.name)}") from None

        entry = {"account": account, "format": fmt, "input_sha256": sha,
                 "statement_sha256": hashlib.sha256(out).hexdigest(), "lines": len(stmt.transactions),
                 "start": stmt.start.isoformat(), "end": stmt.end.isoformat()}
        with _recorded(folder, "import") as save:
            if any(_imported(e, sha) for e in save.entries):
                return Imported(account, sha, 0, "", "", already=True)  # R39: nothing changes
            if any(e.event == "import" and e.data.get("account") == account for e in save.entries):
                raise CaseStoreError(f"account {account} already has a statement in this case; "
                                     "import another account with --account")
            # Files left by an import whose entry is not in the log are replaced.
            for left in (folder / INPUTS).glob(sha + ".*"):
                if left != inp and left.name.split(".", 1)[0] == sha:
                    save.undo.append(_restorer(left, INPUT_MODE))
                    left.unlink()
            save.undo.append(_restorer(inp, INPUT_MODE))
            _write_private(inp, data, INPUT_MODE)
            save.undo.append(_restorer(statement))
            _write_private(statement, out)
            save.data = entry
    return Imported(account, sha, entry["lines"], entry["start"], entry["end"])


def _imported(entry, sha: str) -> bool:
    return entry.event == "import" and entry.data.get("input_sha256") == sha


# --- purge (R35) -----------------------------------------------------------------------


@dataclass(frozen=True)
class Purged:
    case: str
    purged_at: str  # UTC
    by: str         # the operating-system user


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user name the platform can give (a container's bare uid)
        return f"uid {os.getuid()}" if hasattr(os, "getuid") else "unknown"


def _remove_tree(path: Path) -> None:
    """Delete `path` and everything under it. An input file is read-only (0400); on Windows
    a read-only file cannot be deleted, so it is made writable first."""

    def writable_then_retry(func, p, _exc):
        os.chmod(p, 0o600 if not os.path.isdir(p) else DIR_MODE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable_then_retry)
    else:  # pragma: no cover - Python 3.11
        shutil.rmtree(path, onerror=writable_then_retry)


def _append_ledger(root: Path, line: dict[str, str]) -> None:
    """Append one line to the firm-level purge ledger, owner-only, and make it durable."""
    path = root / PURGE_LEDGER
    data = (json.dumps(line, sort_keys=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), FILE_MODE)
    try:
        if os.fstat(fd).st_mode & 0o777 != FILE_MODE:
            os.chmod(path, FILE_MODE)
        os.write(fd, data)  # one short write in append mode: lines from two purges never mix
        os.fsync(fd)
    finally:
        os.close(fd)


def purge_target(case_id: str, root: Path | None = None) -> Path:
    """The folder a purge of `case_id` would delete: the case, or what an interrupted purge
    left of it. CaseStoreError when there is neither, so nothing is asked or recorded."""
    if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id):
        raise CaseStoreError("a case id is 16 lowercase hex characters, as `cma case new` prints it")
    root = ensure_root(root)
    for folder in (root / CASES / case_id, root / CASES / f"{PURGING}{case_id}"):
        if (folder / PROFILE).is_file() or (folder.name.startswith(PURGING) and folder.is_dir()):
            return folder
    raise CaseStoreError(f"no case {case_id} under {root}")


def purge_case(case_id: str, *, root: Path | None = None, wait: float | None = None) -> Purged:
    """Delete every file of a case, and record in the firm-level ledger only the case id,
    the purge time and who purged it (R35): nothing from the profile, the statements, the
    log or the label reaches the ledger.

    The case is taken as a writer first, so a save under way in another window finishes
    or the purge is refused (R42); the log need not be intact, since a broken case can be
    purged too. Holding the case, the folder is renamed out of use (cases/.purging-<id>),
    so no command finds the case again, and then deleted. The ledger line is written only
    once every file is gone. A purge stopped part-way is finished by purging the same id
    again, which deletes what is left and then records it."""
    target = purge_target(case_id, root)
    root = target.parent.parent
    folder = root / CASES / case_id
    gone = root / CASES / f"{PURGING}{case_id}"
    if target == folder:
        try:
            _take_out_of_use(folder, gone, wait)
        except FileNotFoundError:
            # Another purge moved the case first (it was found before that purge renamed
            # it). Never recreate the folder: finish what that purge left, if anything.
            if not gone.is_dir():
                raise CaseStoreError(f"no case {case_id} under {root}") from None
    # The emptied folder stays, as the mark of an unfinished purge, until the ledger line is
    # written; a purge stopped at any step is finished by purging the same id again.
    try:
        for child in list(gone.iterdir()):
            if child.is_dir() and not child.is_symlink():
                _remove_tree(child)
            else:
                _unlink_writable(child)
    except OSError as e:
        what = e.strerror or type(e).__name__
        raise CaseStoreError(f"case {case_id} is out of use but some of its files could not be deleted "
                             f"({what}); purge again to finish. Nothing was recorded in the ledger") from None
    record = Purged(case_id, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), _os_user())
    try:
        if not _ledger_records(root, case_id):  # one line per case, however often it is retried
            _append_ledger(root, {"case": record.case, "purged_at": record.purged_at, "by": record.by})
        gone.rmdir()
    except OSError as e:
        what = e.strerror or type(e).__name__
        raise CaseStoreError(f"case {case_id}'s files are deleted but the purge could not be finished "
                             f"({what}); purge again to record it in the ledger") from None
    return record


def _take_out_of_use(folder: Path, gone: Path, wait: float | None) -> None:
    """Holding the case as a writer, rename it out of use. FileNotFoundError when the case
    folder is gone (another purge moved it), so nothing is made in its place."""
    log = caselog.for_case(folder)
    if not (folder / PROFILE).is_file():
        raise FileNotFoundError(str(folder))
    # Lock files are made only for a case that lacks them; a folder that vanished since is
    # then never recreated (the writer raises FileNotFoundError).
    create = not (log.gate_path.exists() and log.lock_path.exists())
    moved = False
    with log.writer(wait=wait, create=create):
        if not (folder / PROFILE).is_file():
            raise FileNotFoundError(str(folder))
        try:
            os.replace(folder, gone)  # out of use while still held: no writer slips in
            moved = True
        except PermissionError:
            if os.name != "nt":
                raise
    if not moved:  # Windows will not rename a folder whose lock files are open
        os.replace(folder, gone)


def _unlink_writable(path: Path) -> None:
    try:
        path.unlink()
    except PermissionError:  # Windows: a read-only input file (0400)
        os.chmod(path, FILE_MODE)
        path.unlink()


def _ledger_records(root: Path, case_id: str) -> bool:
    """Whether the purge ledger already holds a line for `case_id`."""
    try:
        text = (root / PURGE_LEDGER).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    for line in text.splitlines():
        with suppress(ValueError):
            if json.loads(line).get("case") == case_id:
                return True
    return False
