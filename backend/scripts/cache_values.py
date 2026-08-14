"""Fills in cached values for every formula cell of a generated .xlsx.

Why this is necessary, not cosmetic: openpyxl writes a formula cell as
`<c r="B5"><f>SUMIFS(...)</f><v /></c>` — the formula, then an EMPTY
cached value. Anything that reads the file without evaluating formulas
itself therefore sees blanks: pandas, `load_workbook(data_only=True)`,
file previewers, and — the reason this module exists — Excel charts,
which render as empty frames because every cell they point at is blank.
Excel/Sheets do recalculate on open (workbook.calculation.fullCalcOnLoad
is set too, belt and suspenders), but a chart the user can't see until
they open the real app is not a delivered chart.

The usual fix is the xlsx skill's scripts/recalc.py, which drives
LibreOffice to recalculate and re-save. That hangs indefinitely in this
sandbox — reproducibly, on the specific pattern of a second soffice
invocation reusing an already-initialized user profile, which is exactly
what recalc.py does (verified in isolation: a trivial 3-cell workbook
hangs the same way, so it isn't this workbook or its formulas). This
module does the same job in pure Python via the `formulas` package.

Post-processing the saved XML directly, rather than going through
openpyxl, is deliberate: openpyxl's object model has no way to hold a
formula and its cached value at the same time, so a round-trip would
have to discard one or the other.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

EXCEL_ERRORS = {"#VALUE!", "#DIV/0!", "#REF!", "#NAME?", "#NULL!", "#NUM!", "#N/A"}

# <c r="B5" s="13"><f>...</f><v /></c> — the <f> body can't contain a raw
# '<' (XML-escaped by the writer), so </f> is an unambiguous terminator.
_CELL_RE = re.compile(
    r'(?P<open><c r="(?P<coord>[A-Z]+\d+)"(?P<attrs>[^>]*)>)'
    r"(?P<formula><f>.*?</f>)"
    r"(?P<value><v\s*/>|<v>.*?</v>)?"
    r"(?P<close></c>)",
    re.DOTALL,
)
_T_ATTR_RE = re.compile(r'\s+t="[^"]*"')


def _scalar(value):
    """formulas returns numpy arrays/sentinels; reduce to a plain scalar."""
    if hasattr(value, "flatten"):
        flat = value.flatten()
        if len(flat) == 0:
            return None
        value = flat[0]
    return value


def _rendered(value) -> tuple[str, str] | None:
    """(t_attribute, inner_v_text) for a computed value, or None to leave
    the cell without a cached value (blank, error-free, recalculated on
    open like any normal spreadsheet)."""
    value = _scalar(value)
    if value is None:
        return None

    if isinstance(value, bool):
        return ' t="b"', "1" if value else "0"

    if isinstance(value, (int, float)):
        # numpy floats included — float() normalizes them, and repr keeps
        # full precision so the cached value matches what Excel computes.
        return "", repr(float(value))

    text = str(value)
    if text in EXCEL_ERRORS:
        return ' t="e"', text
    # formulas' sentinel for an empty cell stringifies to something like
    # "<Empty>"; treat anything non-numeric and bracketed as blank rather
    # than writing a literal string that would look like real data.
    if text.startswith("<") and text.endswith(">"):
        return None
    return ' t="str"', escape(text)


def _sheet_xml_by_name(archive: zipfile.ZipFile) -> dict[str, str]:
    """Maps sheet name -> path of its worksheet XML inside the archive,
    resolved through the workbook's relationships (sheet order and
    sheetN.xml numbering are not guaranteed to agree)."""
    workbook = archive.read("xl/workbook.xml").decode("utf-8")
    rels = archive.read("xl/_rels/workbook.xml.rels").decode("utf-8")

    target_by_rid = {
        m.group("rid"): m.group("target")
        for m in re.finditer(
            r'<Relationship[^>]*Target="(?P<target>[^"]*)"[^>]*Id="(?P<rid>[^"]*)"',
            rels,
        )
    }
    target_by_rid.update(
        {
            m.group("rid"): m.group("target")
            for m in re.finditer(
                r'<Relationship[^>]*Id="(?P<rid>[^"]*)"[^>]*Target="(?P<target>[^"]*)"',
                rels,
            )
        }
    )

    result = {}
    for m in re.finditer(r'<sheet name="(?P<name>[^"]*)"[^>]*r:id="(?P<rid>[^"]*)"', workbook):
        target = target_by_rid.get(m.group("rid"))
        if not target:
            continue
        path = target.lstrip("/")
        if not path.startswith("xl/"):
            path = f"xl/{path}"
        # Sheet names are XML-escaped in workbook.xml (& -> &amp;).
        name = (
            m.group("name")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
        )
        result[name] = path
    return result


def inject_cached_values(path: Path) -> dict:
    """Recomputes every formula in `path` and rewrites the file in place
    with those results stored as cached values. Returns a summary dict:
    formulas seen, values written, and any cells that evaluated to an
    Excel error (which callers should treat as a failed build)."""
    import formulas  # imported lazily — heavy (pulls numpy/scipy)

    model = formulas.ExcelModel().loads(str(path)).finish()
    solution = model.calculate()

    # "'[Finanzas.xlsx]RESUMEN MENSUAL'!B5" -> ("RESUMEN MENSUAL", "B5").
    # The book name is matched loosely on purpose: `formulas` uppercases
    # the sheet name but leaves the file name's original case, and only
    # one workbook is ever loaded here, so there's nothing to disambiguate.
    key_re = re.compile(r"^'\[[^\]]*\](?P<sheet>[^']*)'!(?P<coord>[A-Z]+\d+)$")
    computed: dict[tuple[str, str], object] = {}
    for key, cell in solution.items():
        m = key_re.match(key)
        if m:
            computed[(m.group("sheet"), m.group("coord"))] = getattr(cell, "value", None)

    with zipfile.ZipFile(path) as archive:
        sheet_paths = _sheet_xml_by_name(archive)
        contents = {name: archive.read(name) for name in archive.namelist()}

    stats = {"formulas": 0, "written": 0, "errors": []}

    for sheet_name, xml_path in sheet_paths.items():
        if xml_path not in contents:
            continue
        sheet_key = sheet_name.upper()
        xml = contents[xml_path].decode("utf-8")

        def replace(match: re.Match) -> str:
            stats["formulas"] += 1
            coord = match.group("coord")
            rendered = _rendered(computed.get((sheet_key, coord)))
            attrs = _T_ATTR_RE.sub("", match.group("attrs"))
            if rendered is None:
                return f'<c r="{coord}"{attrs}>{match.group("formula")}{match.group("close")}'
            t_attr, inner = rendered
            if inner in EXCEL_ERRORS:
                stats["errors"].append(f"{sheet_name}!{coord} = {inner}")
            stats["written"] += 1
            return (
                f'<c r="{coord}"{attrs}{t_attr}>'
                f'{match.group("formula")}<v>{inner}</v>{match.group("close")}'
            )

        contents[xml_path] = _CELL_RE.sub(replace, xml).encode("utf-8")

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in contents.items():
            out.writestr(name, data)
    shutil.move(str(tmp_path), str(path))

    return stats
