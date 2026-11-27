"""What the case app changes: a new case, a case's details, an imported statement. Each goes
through the same store functions as the commands, so every change is checked and logged in
the case the same way; a refusal comes back as a sentence for the page, never a traceback."""

from __future__ import annotations

import re
import secrets
import shutil
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .. import case as caselog
from .. import store
from ..categorise import RulesetError
from ..firm import FirmConfigError
from ..ingest import StatementError
from ..metrics import AmountError, ReconciliationError

# The banks and file kinds the import form offers: (form value, what the analyst sees,
# the store's format, whether the file carries no balance and needs the opening balance).
BANKS: list[tuple[str, str, str, bool]] = [
    ("hsbc", "HSBC (CSV download)", "bank-csv:hsbc", True),
    ("barclays", "Barclays (CSV download)", "bank-csv:barclays", False),
    ("lloyds", "Lloyds (CSV download)", "bank-csv:lloyds", False),
    ("natwest", "NatWest (CSV download)", "bank-csv:natwest", False),
    ("starling", "Starling (CSV download)", "bank-csv:starling", False),
    ("monzo", "Monzo (CSV export)", "monzo", False),
    ("openbanking", "Open Banking file (JSON)", "openbanking", False),
    ("truelayer", "TrueLayer file (JSON)", "truelayer", False),
    ("csv", "Standard statement CSV (date, description, amount, balance)", "csv", False),
]
BANK_FORMATS = {key: fmt for key, _label, fmt, _needs in BANKS}
STATEMENT_BALANCE_FORMATS = frozenset({"openbanking", "truelayer"})
OPENING_BALANCE_FORMATS = frozenset({"monzo"}) | {f for f in BANK_FORMATS.values() if f.startswith("bank-csv:")}
MAX_UPLOAD = 25 * 1024 * 1024  # bytes: a year of statements is far smaller
ACCOUNT_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
UPLOADS = ".uploads"


class Refused(ValueError):
    """A change the app did not make, with the sentence that says why."""


@dataclass(frozen=True)
class Done:
    message: str
    case_id: str


def explain(e: BaseException) -> str:
    """The sentence a refusal shows on the page."""
    if isinstance(e, Refused):
        return str(e)
    if isinstance(e, caselog.CaseLogBroken):
        return (f"The case's record of changes is broken at entry {e.first_bad} ({e.reason}). Nothing was "
                "changed. Ask your administrator to check the case.")
    if isinstance(e, caselog.SaveNotConfirmed):
        return f"Whether the change was saved is not confirmed ({e}). Ask your administrator to check the case."
    if isinstance(e, (caselog.CaseLocked, caselog.LogConflict)):
        return "Another window was saving to this case at the same moment. Nothing was changed; try again."
    if isinstance(e, ReconciliationError):
        return f"The statement's figures do not add up: {e}. Nothing was changed."
    if isinstance(e, (store.CaseStoreError, FirmConfigError, RulesetError, StatementError, AmountError)):
        text = str(e)
        text = text.replace("--opening-balance", "the opening balance").replace("--statement-balance",
                                                                                 "the statement balance")
        for key in store.PROFILE_FIELDS:
            text = text.replace(key, key.replace("_", " "))
        return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")
    if isinstance(e, OSError):
        return f"A file could not be read or written ({e.strerror or type(e).__name__}). Nothing was changed."
    return "Something went wrong and nothing was changed."


def _text(fields: dict[str, str], key: str, limit: int = 200) -> str:
    value = (fields.get(key) or "").strip()
    if len(value) > limit:
        raise Refused(f"The {key.replace('_', ' ')} is too long (at most {limit} characters).")
    return value


def _names(fields: dict[str, str], key: str) -> list[str]:
    """One name a line, blank lines left out."""
    raw = fields.get(key) or ""
    names = [line.strip() for line in raw.replace("\r", "").split("\n") if line.strip()]
    if len(names) > 50:
        raise Refused(f"Give at most 50 {key.replace('_', ' ')}.")
    return names


def _amount(text: str, what: str) -> str | None:
    """A plain amount as typed, with any pound sign or spaces taken off, or None when blank."""
    cleaned = text.replace("£", "").replace(" ", "").strip()
    if not cleaned:
        return None
    try:
        Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        raise Refused(f"The {what} should be an amount such as 1,234.56 or -250.00.") from None
    return cleaned


def new_case(fields: dict[str, str]) -> tuple[Done, str | None]:
    """Make a case from the new-case form. Returns what was done and any warning that the
    case folder lies in a cloud-synced folder (the same warning the command gives)."""
    label = _text(fields, "label")
    business = _text(fields, "business_name")
    demo = fields.get("demo") == "yes"
    warned: list[str] = []
    case_id = store.new_case(label=label or None, demo=demo, warn=warned.append)
    if business:
        store.update_profile(store.case_folder(case_id), lambda p: {**p, "business_name": business})
    return Done("Case made. Add the business's details and statements next.", case_id), (warned[0] if warned else None)


def save_details(case_id: str, fields: dict[str, str]) -> Done:
    """Save the case-details form: the profile fields an analyst fills in."""
    folder = store.case_folder(case_id)
    stagger = (fields.get("vat_stagger") or "").strip()
    if stagger not in ("", "1", "2", "3"):
        raise Refused("The VAT stagger group is 1, 2 or 3, from the VAT certificate.")
    repayment = _amount(fields.get("proposed_monthly_repayment") or "", "proposed monthly repayment")
    changes = {
        "label": _text(fields, "label") or None,
        "business_name": _text(fields, "business_name") or None,
        "directors": _names(fields, "directors"),
        "shareholders": _names(fields, "shareholders"),
        "own_accounts": _names(fields, "own_accounts"),
        "vat_stagger": int(stagger) if stagger else None,
        "proposed_monthly_repayment": repayment,  # checked by the store, which reads 1,500.00 but not 1500,50
    }
    store.update_profile(folder, lambda p: {**p, **changes})
    return Done("Details saved.", case_id)


def _safe_name(name: str) -> str:
    """The uploaded file's own name, kept only as letters, digits, dots, dashes and underscores,
    so refusals can name the file the analyst chose."""
    base = Path(name.replace("\\", "/")).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "statement"
    return cleaned[:80]


def import_statement(case_id: str, fields: dict[str, str], filename: str, data: bytes) -> Done:
    """Import one uploaded statement into the case through store.import_file. The upload is
    written to a private folder inside the case, imported, and removed whatever happens."""
    folder = store.case_folder(case_id)
    bank = fields.get("bank") or ""
    if bank not in BANK_FORMATS:
        raise Refused("Choose the bank or kind of file.")
    fmt = BANK_FORMATS[bank]
    if not data:
        raise Refused("Choose the statement file to import.")
    if len(data) > MAX_UPLOAD:
        raise Refused("The file is too large to be a statement (more than 25 MB).")
    account = (fields.get("account") or "").strip().lower().replace(" ", "-")
    if not ACCOUNT_KEY.fullmatch(account):
        raise Refused("Give the account a short name of letters and digits, such as current or savings.")
    opening = _amount(fields.get("opening_balance") or "", "opening balance")
    statement_balance = _amount(fields.get("statement_balance") or "", "statement balance")
    at = (fields.get("at") or "").strip() or None
    if fmt not in OPENING_BALANCE_FORMATS:
        opening = None  # the field is shown for every bank; only these read it
    if fmt not in STATEMENT_BALANCE_FORMATS:
        statement_balance, at = None, None
    # Beside the cases, not in one: a case folder holds only what the store wrote.
    uploads = folder.parent.parent / UPLOADS
    uploads.mkdir(mode=0o700, exist_ok=True)
    holder = uploads / secrets.token_hex(8)
    holder.mkdir(mode=0o700)
    try:
        src = holder / _safe_name(filename)
        with open(src, "xb") as f:
            f.write(data)
        r = store.import_file(folder, src, fmt, account=account, opening_balance=opening,
                              statement_balance=statement_balance, at=at)
    finally:
        shutil.rmtree(holder, ignore_errors=True)
    if r.already:
        return Done("That file was already imported; the case is unchanged.", case_id)
    extra = ""
    if r.pending:
        extra += f" {r.pending} pending transaction{'s' if r.pending != 1 else ''} left out."
    if r.repeated:
        extra += f" {r.repeated} repeated transaction{'s' if r.repeated != 1 else ''} kept once."
    return Done(f"Imported account {r.account}: {r.lines} transactions from {r.start} to {r.end}.{extra}", case_id)
