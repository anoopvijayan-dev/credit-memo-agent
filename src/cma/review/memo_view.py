"""The memo's text (pack.py's markdown) as a document: headings, quoted notes, lists and
paragraphs. Only the few forms the pack writes are read; everything else is shown as plain
text. Every piece of text is escaped. Used by the review's preview and the app's memo page."""

from __future__ import annotations

from html import escape


def escape_(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def memo_html(text: str) -> str:
    """The pack's memo.md as a page: headings, quoted notes, lists and paragraphs. Only the
    few forms pack.py writes are read; everything else is shown as plain text, escaped."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if para:
            out.append(f"<p>{escape_(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{escape_(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("## "):
            flush()
            out.append(f"<h3>{escape_(stripped[3:])}</h3>")
        elif stripped.startswith("# "):
            flush()  # the memo's own title; the page has one
        elif stripped.startswith("> "):
            flush()
            out.append(f'<p class="memo-note">{escape_(stripped[2:])}</p>')
        elif stripped.startswith("- "):
            if para:
                flush()
            items.append(stripped[2:])
        elif stripped.startswith("_") and stripped.endswith("_") and len(stripped) > 2:
            flush()
            out.append(f'<p class="memo-empty">{escape_(stripped[1:-1])}</p>')
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return '<div class="memo">' + "".join(out) + "</div>"
