"""AcroForm enumeration.

This is the foundation every later stage inherits, so it is deliberately literal about
the two traps in docs/00-DOMAIN.md:

  * §3.2 a button's "on" value is per-field and is read out of /AP -> /N. It is never
    assumed to be /Yes.
  * §3.3 fields are keyed by fully qualified name. A dict keyed on short names silently
    loses fields on DL 142, which has several fields whose short name is "0".
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.generic import DictionaryObject, IndirectObject

FT_TO_TYPE = {
    "/Tx": "text",
    "/Btn": "button",
    "/Ch": "choice",
    "/Sig": "signature",
}


@dataclass
class Widget:
    """One annotation on one page. A field usually has exactly one."""

    page: int | None
    rect: list[float] | None
    on_value: str | None = None
    states: list[str] = dc_field(default_factory=list)


@dataclass
class FieldRecord:
    qualified_name: str
    ft: str | None  # raw /FT, None for container nodes that carry no type
    type: str  # text | button | choice | signature | container
    tooltip: str | None
    max_len: int | None
    flags: int
    value: Any
    widgets: list[Widget] = dc_field(default_factory=list)
    ft_inherited: bool = False

    @property
    def page(self) -> int | None:
        return self.widgets[0].page if self.widgets else None

    @property
    def rect(self) -> list[float] | None:
        return self.widgets[0].rect if self.widgets else None

    @property
    def on_value(self) -> str | None:
        for w in self.widgets:
            if w.on_value:
                return w.on_value
        return None

    @property
    def states(self) -> list[str]:
        return self.widgets[0].states if self.widgets else []

    @property
    def is_radio(self) -> bool:
        return self.ft == "/Btn" and bool(self.flags & (1 << 15))

    @property
    def is_pushbutton(self) -> bool:
        return self.ft == "/Btn" and bool(self.flags & (1 << 16))

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualifiedName": self.qualified_name,
            "type": self.type,
            "page": self.page,
            "rect": self.rect,
            "onValue": self.on_value,
            "states": self.states,
            "tooltip": self.tooltip,
            "maxLen": self.max_len,
            "widgetCount": len(self.widgets),
        }


@dataclass
class FormInfo:
    page_count: int
    has_xfa: bool
    need_appearances: bool | None
    fields: list[FieldRecord]

    def of_type(self, type_name: str) -> list[FieldRecord]:
        return [f for f in self.fields if f.type == type_name]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.fields),
            "text": len(self.of_type("text")),
            "button": len(self.of_type("button")),
            "container": len(self.of_type("container")),
            "tooltips": sum(1 for f in self.fields if f.tooltip),
        }


def _deref(obj: Any) -> Any:
    while isinstance(obj, IndirectObject):
        obj = obj.get_object()
    return obj


def _text(obj: Any) -> str | None:
    obj = _deref(obj)
    if obj is None:
        return None
    s = str(obj)
    return s if s != "" else None


def _page_index_map(reader: PdfReader) -> dict[int, int]:
    """Map annotation object number -> page index."""
    mapping: dict[int, int] = {}
    for page_index, page in enumerate(reader.pages):
        annots = _deref(page.get("/Annots"))
        if not annots:
            continue
        for annot in annots:
            if isinstance(annot, IndirectObject):
                mapping[annot.idnum] = page_index
            else:
                obj = _deref(annot)
                ref = getattr(obj, "indirect_reference", None)
                if ref is not None:
                    mapping[ref.idnum] = page_index
    return mapping


def _widget_of(node: DictionaryObject, page_map: dict[int, int]) -> Widget:
    ref = getattr(node, "indirect_reference", None)
    page = page_map.get(ref.idnum) if ref is not None else None
    rect_raw = _deref(node.get("/Rect"))
    rect = [float(v) for v in rect_raw] if rect_raw else None

    states: list[str] = []
    on_value: str | None = None
    ap = _deref(node.get("/AP"))
    if isinstance(ap, DictionaryObject):
        normal = _deref(ap.get("/N"))
        if isinstance(normal, DictionaryObject):
            states = [str(k) for k in normal.keys()]
            on_states = [s for s in states if s != "/Off"]
            # Exactly one non-/Off state is the normal case for these forms. If a widget
            # somehow has several, take the first deterministically rather than guessing.
            if on_states:
                on_value = sorted(on_states)[0]
    return Widget(page=page, rect=rect, on_value=on_value, states=states)


def _walk(
    node: Any,
    parent_name: str | None,
    inherited_ft: str | None,
    page_map: dict[int, int],
    out: list[FieldRecord],
    seen: set[int],
) -> None:
    node = _deref(node)
    if not isinstance(node, DictionaryObject):
        return
    ref = getattr(node, "indirect_reference", None)
    if ref is not None:
        if ref.idnum in seen:
            return
        seen.add(ref.idnum)

    short = _text(node.get("/T"))
    qualified = short if parent_name is None else f"{parent_name}.{short}"

    own_ft = _text(node.get("/FT"))
    ft = own_ft or inherited_ft

    kids = _deref(node.get("/Kids")) or []
    named_kids = []
    widget_kids = []
    for kid in kids:
        kid_obj = _deref(kid)
        if not isinstance(kid_obj, DictionaryObject):
            continue
        if _text(kid_obj.get("/T")) is not None:
            named_kids.append(kid_obj)
        else:
            widget_kids.append(kid_obj)

    if short is not None:
        widgets: list[Widget] = []
        if node.get("/Rect") is not None:  # field merged with its own widget annotation
            widgets.append(_widget_of(node, page_map))
        for kid_obj in widget_kids:
            widgets.append(_widget_of(kid_obj, page_map))

        max_len = _deref(node.get("/MaxLen"))
        flags = _deref(node.get("/Ff")) or 0
        out.append(
            FieldRecord(
                qualified_name=qualified,
                ft=ft,
                type=FT_TO_TYPE.get(ft or "", "container"),
                tooltip=_text(node.get("/TU")),
                max_len=int(max_len) if max_len is not None else None,
                flags=int(flags),
                value=_text(node.get("/V")),
                widgets=widgets,
                ft_inherited=own_ft is None and ft is not None,
            )
        )

    next_parent = qualified if short is not None else parent_name
    for kid_obj in named_kids:
        _walk(kid_obj, next_parent, ft, page_map, out, seen)


def read_form(pdf_path: str | Path) -> FormInfo:
    """Enumerate every field node in a PDF's AcroForm, keyed by qualified name."""
    reader = PdfReader(str(pdf_path))
    root = reader.trailer["/Root"]
    acro = _deref(root.get("/AcroForm")) or DictionaryObject()

    page_map = _page_index_map(reader)
    fields: list[FieldRecord] = []
    seen: set[int] = set()
    for top in _deref(acro.get("/Fields")) or []:
        _walk(top, None, None, page_map, fields, seen)

    need = _deref(acro.get("/NeedAppearances"))
    return FormInfo(
        page_count=len(reader.pages),
        has_xfa=acro.get("/XFA") is not None,
        need_appearances=bool(need) if need is not None else None,
        fields=fields,
    )
