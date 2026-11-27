"""Every file the tool writes through a raw descriptor is opened in binary mode.

On Windows, os.open without O_BINARY gives a text-mode descriptor: each b"\\n" written becomes
b"\\r\\n". The case log, packs and pseudonymised copies are hashed byte for byte, so a text-mode
write makes a case unusable there: `cma case new` refused every save with "the log changed
while this save was being recorded". Linux and macOS never translate, so no behavioural test
on them can see this; the rule is checked in the source instead.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "cma"
WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT"}


def _names(node: ast.AST) -> set[str]:
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} | {
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _os_open_calls():
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "os" and len(node.args) >= 2):
                yield path.relative_to(SRC.parent).as_posix(), node.lineno, node.args[1]


def test_the_source_has_raw_opens_to_check():
    assert sum(1 for _ in _os_open_calls()) >= 5


@pytest.mark.parametrize("where,line,flags", list(_os_open_calls()), ids=lambda v: str(v) if not isinstance(v, ast.AST) else "")
def test_a_raw_open_that_can_write_is_binary(where, line, flags):
    names = _names(flags)
    if not names & WRITE_FLAGS:
        return  # read-only or a lock probe: nothing is written through it
    assert "O_BINARY" in names, f"{where}:{line} opens a file for writing without os.O_BINARY (text mode on Windows)"
