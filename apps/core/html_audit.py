"""Content checks for a rendered page: the failures that still return HTTP 200.

`audit_pages` proves that every route renders. It cannot prove that the page a
visitor gets is worth reading, because Django templates fail silently: a
reference to a missing attribute renders as an empty string, and a raw
dictionary rendered into a cell looks like a success to any status-code test.

This module walks the *rendered HTML* with the standard parser and reports the
things a human reviewer would flag:

* a raw Python value (`{'version': 1, ...}`) leaked into the page;
* an empty table cell, or a header cell without `scope`;
* a table without a caption;
* a duplicate `id`, or an `aria-labelledby`/`href="#..."` pointing at an id that
  does not exist (broken anchors and broken accessible names);
* the literals `None`, `False`, `[]`, `{}`, `nan`, `TODO`, `XXX`, `lorem ipsum`
  in visible text, which is what a missing value looks like to a reader;
* a control with no accessible name.

It is deliberately a list of *heuristics with no false positives tolerated*: if a
check fires, something is wrong. `check()` returns findings, so it can be unit
tested and used both by the crawl command and by tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Containers whose text is not read by a person.
OPAQUE_TAGS = {"script", "style", "template", "svg"}

# Verbatim examples (code blocks, command lines, sample URLs) are deliberate.
VERBATIM_TAGS = {"pre", "code", "samp", "kbd"}

# A missing value, in a *data* position (a table cell or a description-list
# value). Prose is allowed to say "None of the tenders…"; a cell is not allowed
# to say "None".
BAD_DATA_TEXT = re.compile(
    r"(?<![\w-])(None|False|True|nan|NaN|undefined|XXX|TODO|FIXME|\[\]|\{\})(?![\w-])"
)

# Block containers: a paragraph or list item whose *entire* content is one of
# these literals is a missing value, whatever the surrounding prose is doing.
BLOCK_TAGS = {"p", "li", "dd", "dt", "h2", "h3", "h4", "span", "div", "figcaption"}

# Unambiguous junk anywhere in visible text.
BAD_PROSE_TEXT = re.compile(r"(?<![\w-])(TODO|FIXME|lorem ipsum|\[\]|\{\})(?![\w-])", re.I)

# Attributes that hold a space-separated list of element ids.
ID_REF_ATTRS = ("aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-flowto")


@dataclass
class Finding:
    check: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.check}: {self.detail}"


@dataclass
class _Field:
    """An input/select/textarea and whatever might give it an accessible name."""

    tag: str
    attrs: dict
    wrapped_in_label: bool = False


@dataclass
class _Table:
    has_caption: bool = False
    bad_headers: list[str] = field(default_factory=list)
    empty_cells: int = 0
    rows: int = 0


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: dict[str, int] = {}
        self.id_refs: list[tuple[str, str]] = []
        self.hrefs: list[str] = []
        self.tables: list[_Table] = []
        self.fields: list[_Field] = []
        self.labels_for: set[str] = set()
        self.aria_labels: set[str] = set()
        self.visible_text: list[str] = []
        self.verbatim_text: list[str] = []
        self.lone_literals: list[str] = []
        self._last_stripped: str = ""
        self.data_text: list[str] = []
        self.h1_count = 0
        self.h2_no_text: int = 0
        self._stack: list[str] = []
        self._caption_depth: int = 0
        self._open_table: _Table | None = None
        self._in_cell: str | None = None
        self._cell_text: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _visible(self) -> bool:
        return not any(tag in OPAQUE_TAGS for tag in self._stack)

    def _text_since(self, tag: str) -> bool:
        return tag in self._stack

    # -- parser hooks ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        attrs = {k: (v or "") for k, v in attrs}
        if "id" in attrs:
            self.ids[attrs["id"]] = self.ids.get(attrs["id"], 0) + 1
        for ref in ID_REF_ATTRS:
            for target in attrs.get(ref, "").split():
                self.id_refs.append((ref, target))
        if "aria-label" in attrs:
            self.aria_labels.add(attrs["aria-label"])
        if tag == "a" and "href" in attrs:
            self.hrefs.append(attrs["href"])
        if tag == "h1":
            self.h1_count += 1
        if tag == "label" and "for" in attrs:
            self.labels_for.add(attrs["for"])

        if tag in ("input", "select", "textarea"):
            kind = attrs.get("type", "")
            if not (tag == "input" and kind in ("hidden", "submit", "button", "reset", "image")):
                self.fields.append(
                    _Field(tag, attrs, wrapped_in_label=self._text_since("label"))
                )
        if tag == "table":
            self._open_table = _Table()
        elif tag == "caption" and self._open_table is not None:
            self._open_table.has_caption = True
        elif tag == "tr" and self._open_table is not None:
            self._open_table.rows += 1
        elif tag == "th":
            if self._open_table is not None and "scope" not in attrs:
                self._open_table.bad_headers.append("th without scope")
            if self._open_table is None and attrs.get("id"):
                # a layout column header, still fine
                pass
        elif tag in ("td", "th"):
            self._in_cell, self._cell_text = tag, []

        self._stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        # Self-closing: no stack change, but the same bookkeeping.
        self.handle_starttag(tag, attrs)
        self._stack.pop()

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS and self._last_stripped:
            if BAD_DATA_TEXT.fullmatch(self._last_stripped):
                self.lone_literals.append(self._last_stripped)
            self._last_stripped = ""
        if tag in ("td", "th") and self._in_cell:
            text = "".join(self._cell_text).strip()
            if self._open_table is not None and not text:
                self._open_table.empty_cells += 1
            self.data_text.append(text)
            self._in_cell, self._cell_text = None, []
        if tag in ("dd", "dt", "output"):
            self.data_text.append("".join(self._cell_text).strip())
            self._cell_text = []
        if tag == "table" and self._open_table is not None:
            self.tables.append(self._open_table)
            self._open_table = None
        if tag in self._stack:
            # Unwind to the matching open tag (tolerates sloppy markup).
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if data.strip():
            self._last_stripped = data.strip()
        if self._in_cell is not None:
            self._cell_text.append(data)
        self._cell_text.append(data) if self._text_since("dd") else None
        if self._visible():
            if self._text_since("pre") or self._text_since("code"):
                self.verbatim_text.append(data)
            self.visible_text.append(data)


def check(html: str) -> list[Finding]:
    """Return every content problem found in a rendered page."""
    page = _Page()
    page.feed(html)
    page.close()
    findings: list[Finding] = []

    # 1. Raw Python values. A dict rendered into a template arrives as
    #    `{'key': 'value'}` with the quotes entity-encoded, or as a repr such as
    #    `Decimal('1.00')`. Verbatim examples in <pre>/<code> are exempt.
    prose = " ".join(page.visible_text)
    data = " · ".join(t for t in page.data_text if t)
    raw = re.compile(
        r"\{\s*['\"]\w+['\"]\s*:|\b(?:Decimal|datetime\.date|datetime\.datetime|"
        r"Decimal\()\(|\bDecimal\('|\bdatetime\.datetime\("
    )
    for haystack, label in ((data, "data"), (prose, "text")):
        match = raw.search(haystack)
        if match:
            start = max(0, match.start() - 50)
            findings.append(
                Finding("raw-value", f"in {label}: “{haystack[start:match.end() + 60].strip()}”")
            )

    # 2. Missing values printed as literals in a data position.
    for match in BAD_DATA_TEXT.finditer(data or " "):
        context = data[max(0, match.start() - 50): match.end() + 50].strip()
        findings.append(Finding("missing-value", f"{match.group(0)!r} in “{context}”"))
        if len(findings) > 12:
            break

    # 3. A block whose only content is a missing-value literal.
    for literal in page.lone_literals:
        findings.append(Finding("missing-value", f"“{literal}” stands alone in the page"))

    # 4. Unambiguous junk anywhere in prose.
    for match in BAD_PROSE_TEXT.finditer(prose):
        context = prose[max(0, match.start() - 50): match.end() + 50].strip()
        findings.append(Finding("junk-text", f"{match.group(0)!r} in “{context}”"))

    # 5. Table shape.
    for index, table in enumerate(page.tables, start=1):
        if not table.has_caption:
            findings.append(Finding("table-caption", f"table #{index} has no <caption>"))
        for problem in table.bad_headers:
            findings.append(Finding("table-scope", f"table #{index}: {problem}"))
        if table.empty_cells:
            findings.append(
                Finding("empty-cell", f"table #{index}: {table.empty_cells} empty cell(s)")
            )
        if table.rows and table.rows < 1:  # pragma: no cover - defensive
            findings.append(Finding("table-rows", f"table #{index} has no rows"))

    # 6. Duplicate ids break anchors and ARIA references.
    for element_id, count in page.ids.items():
        if count > 1:
            findings.append(Finding("duplicate-id", f'id="{element_id}" appears {count} times'))

    # 7. References to ids that do not exist.
    for attr, target in page.id_refs:
        if target not in page.ids:
            findings.append(Finding("dangling-reference", f'{attr}="{target}" has no such id'))

    for href in page.hrefs:
        if href.startswith("#") and len(href) > 1 and href[1:] not in page.ids:
            findings.append(Finding("dangling-anchor", f'href="{href}" has no such id'))
        if href == "#":
            findings.append(Finding("dead-link", 'href="#" does nothing'))

    # 8. Controls with no accessible name.
    for fld in page.fields:
        attrs = fld.attrs
        named = (
            attrs.get("aria-label")
            or attrs.get("aria-labelledby")
            or attrs.get("title")
            or (attrs.get("id") and attrs["id"] in page.labels_for)
            or fld.wrapped_in_label
        )
        if not named:
            findings.append(
                Finding("unlabelled-control", f"<{fld.tag} name={attrs.get('name', '?')!r}>")
            )

    # 9. Heading structure.
    if page.h1_count != 1:
        findings.append(Finding("h1-count", f"{page.h1_count} <h1> elements"))

    return findings
