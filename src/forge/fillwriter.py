"""Deterministic PDF writing and rasterisation.

Shared by the calibration sentinel pass (phase 1) and `forge fill` (phase 3). Contains
no model code and never will — this module IS the fill path.

The three traps from docs/00-DOMAIN.md are handled here:
  §3.1 XFA hybrids: set /NeedAppearances and strip stale widget appearance streams so
       the viewer (and poppler) regenerates them from the value.
  §3.2 buttons are set to their per-field discovered on-value, on /V and on each
       widget's /AS. Never /Yes.
  §3.3 field nodes are located by fully qualified name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject, TextStringObject

from .pdfmeta import _deref


def _writer_field_nodes(writer: PdfWriter) -> dict[str, DictionaryObject]:
    """Map fully qualified name -> field node, mirroring pdfmeta's walk exactly."""
    nodes: dict[str, DictionaryObject] = {}

    def walk(node: DictionaryObject, parent: str | None) -> None:
        node = _deref(node)
        if not isinstance(node, DictionaryObject):
            return
        t = node.get("/T")
        short = str(_deref(t)) if t is not None else None
        qualified = short if parent is None else (f"{parent}.{short}" if short else parent)
        if short is not None:
            nodes[qualified] = node
        for kid in _deref(node.get("/Kids")) or []:
            kid_obj = _deref(kid)
            if isinstance(kid_obj, DictionaryObject) and kid_obj.get("/T") is not None:
                walk(kid_obj, qualified if short is not None else parent)

    acro = _deref(writer._root_object["/AcroForm"])
    for top in _deref(acro["/Fields"]) or []:
        walk(top, None)
    return nodes


def write_filled(
    form_path: str | Path,
    out_path: str | Path,
    texts: dict[str, str] | None = None,
    buttons: dict[str, str] | None = None,
) -> list[str]:
    """Write text values and button on-values into a copy of the form.

    `texts` maps qualified name -> string; `buttons` maps qualified name -> the
    field's own discovered on-value (e.g. "/2"). Returns qualified names that could
    not be located — callers must treat a non-empty return as an error, not a warning.
    """
    texts = texts or {}
    buttons = buttons or {}
    writer = PdfWriter(clone_from=str(form_path))
    acro = _deref(writer._root_object["/AcroForm"])
    acro[NameObject("/NeedAppearances")] = BooleanObject(True)

    nodes = _writer_field_nodes(writer)
    missing: list[str] = []

    for qname, value in texts.items():
        node = nodes.get(qname)
        if node is None:
            missing.append(qname)
            continue
        node[NameObject("/V")] = TextStringObject(value)
        # stale appearance streams show the OLD content; drop them so NeedAppearances
        # forces regeneration (docs/00-DOMAIN.md §3.1)
        if "/AP" in node:
            del node[NameObject("/AP")]
        for kid in _deref(node.get("/Kids")) or []:
            kid_obj = _deref(kid)
            if isinstance(kid_obj, DictionaryObject) and "/AP" in kid_obj:
                del kid_obj[NameObject("/AP")]

    for qname, on_value in buttons.items():
        node = nodes.get(qname)
        if node is None:
            missing.append(qname)
            continue
        if not (isinstance(on_value, str) and on_value.startswith("/")):
            raise ValueError(f"button {qname}: on-value must be a name like '/2', got {on_value!r}")
        node[NameObject("/V")] = NameObject(on_value)
        if "/Rect" in node:  # field merged with its widget
            node[NameObject("/AS")] = NameObject(on_value)
        for kid in _deref(node.get("/Kids")) or []:
            kid_obj = _deref(kid)
            if isinstance(kid_obj, DictionaryObject) and kid_obj.get("/T") is None:
                kid_obj[NameObject("/AS")] = NameObject(on_value)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return missing


def render_pages(
    pdf_path: str | Path,
    out_dir: str | Path,
    stem: str,
    dpi: int = 150,
    gray: bool = False,
    crop_px: tuple[int, int, int, int] | None = None,
    page: int | None = None,
) -> list[Path]:
    """Rasterise with pdftoppm — the ground truth for every verification.

    Files come back as `<stem>-page-<i>.png` with a ZERO-based page index, per the
    phase 1 gate. `crop_px` is (x, y, w, h) in output pixels; `page` is zero-based.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"{stem}-tmp"
    fmt = "-gray" if gray else "-png"
    cmd = ["pdftoppm", fmt, "-r", str(dpi)]
    if page is not None:
        cmd += ["-f", str(page + 1), "-l", str(page + 1)]
    if crop_px is not None:
        x, y, w, h = crop_px
        cmd += ["-x", str(x), "-y", str(y), "-W", str(w), "-H", str(h)]
    cmd += [str(pdf_path), str(prefix)]
    subprocess.run(cmd, check=True, capture_output=True)

    ext = "pgm" if gray else "png"
    produced = sorted(out_dir.glob(f"{stem}-tmp-*.{ext}"))
    if not produced:
        raise RuntimeError(f"pdftoppm produced no output for {pdf_path}")
    renamed: list[Path] = []
    for p in produced:
        one_based = int(p.stem.rsplit("-", 1)[-1])
        target = out_dir / f"{stem}-page-{one_based - 1}.{ext}"
        p.replace(target)
        renamed.append(target)
    return renamed


def pgm_payload_diff(a: Path, b: Path) -> int:
    """Count differing payload bytes between two same-size PGM renders."""
    da, db = Path(a).read_bytes(), Path(b).read_bytes()
    if len(da) != len(db):
        return -1
    return sum(1 for x, y in zip(da, db) if x != y)
