"""Rich Text Format (.rtf) report writer — no dependencies.

File > Create Report > Download .rtf (CONTRACT "Model templates, general
grids, shell editing and drawing aids").  ``build_rtf(doc) -> bytes`` takes
EXACTLY the :func:`skyframe.io.docx.build_docx` document (title, subtitle,
meta, paper, landscape, toc, blocks of heading / paragraph / table / image /
page_break) and writes RTF 1.9:

* headings use the built-in ``\\s1..\\s3`` heading styles with
  ``\\outlinelevel`` so Word builds the TOC field from them;
* tables are ``\\trowd .. \\cellx .. \\cell .. \\row`` with a repeated
  header row (``\\trhdr``) and right-aligned numeric columns;
* images are embedded PNG pictures (``{\\pict\\pngblip ...}`` hex) scaled
  to the text width (``\\picwgoal`` / ``\\pichgoal`` in twips at 96 dpi);
* non-ASCII text is written as ``\\uN?`` escapes (UTF-16 code units).

Validation errors raise ``ValueError`` with the same messages as the
.docx writer.
"""

from __future__ import annotations

import binascii
from typing import List

from .docx import _MARGIN, _MAX_BLOCKS, _PAGES, _png_bytes, _png_size

__all__ = ["build_rtf", "MIME", "rtf_escape"]

MIME = "application/rtf"
_TWIP_PX = 15                     # twips per pixel at 96 dpi


def rtf_escape(text) -> str:
    """RTF-escape plain text (\\, {, } and every non-ASCII code unit)."""
    s = "" if text is None else str(text)
    out: List[str] = []
    for ch in s:
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif ch == "\t":
            out.append("\\tab ")
        elif 32 <= o < 128:
            out.append(ch)
        elif o < 32:
            continue
        else:
            data = ch.encode("utf-16-le")
            for k in range(0, len(data), 2):
                u = int.from_bytes(data[k:k + 2], "little")
                if u > 32767:
                    u -= 65536
                out.append(f"\\u{u}?")
    return "".join(out)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def _para(text, style="", align="") -> str:
    return "{\\pard\\plain" + style + align + " " + rtf_escape(text) + \
        "\\par}\n"


_HEAD = {1: "\\s1\\outlinelevel0\\sb240\\sa120\\keepn\\b\\fs32\\cf2",
         2: "\\s2\\outlinelevel1\\sb200\\sa80\\keepn\\b\\fs26\\cf2",
         3: "\\s3\\outlinelevel2\\sb160\\sa60\\keepn\\b\\fs22\\cf2"}


def _table(b: dict, text_w: int) -> str:
    cols = [str(c) for c in (b.get("columns") or [])]
    rows = b.get("rows") or []
    if not cols:
        raise ValueError("table block needs columns")
    if not isinstance(rows, list):
        raise ValueError("table rows must be a list")
    n = len(cols)
    align = list(b.get("align") or [])
    if len(align) != n:
        first = rows[0] if rows else [None] * n
        align = ["r" if isinstance(first[i] if i < len(first) else None,
                                   (int, float)) else "l" for i in range(n)]
    w = max(400, text_w // n)
    bdr = ("\\clbrdrt\\brdrs\\brdrw10\\clbrdrl\\brdrs\\brdrw10"
           "\\clbrdrb\\brdrs\\brdrw10\\clbrdrr\\brdrs\\brdrw10")

    def row(cells, head):
        out = ["\\trowd\\trgaph60\\trleft0" + ("\\trhdr" if head else "")]
        shade = "\\clcbpat3" if head else ""
        out.append("".join(bdr + shade + "\\cellx" + str(w * (i + 1))
                           for i in range(n)))
        for i, v in enumerate(cells):
            al = "\\qr" if align[i] == "r" else ("\\qc" if align[i] == "c"
                                                 else "\\ql")
            out.append("\\pard\\intbl" + al + ("\\b " if head else " ")
                       + rtf_escape(_fmt(v)) + ("\\b0" if head else "")
                       + "\\cell")
        out.append("\\row\n")
        return "".join(out)

    parts = []
    if b.get("caption"):
        parts.append(_para(b["caption"], "\\keepn\\i\\fs18"))
    parts.append(row(cols, True))
    for r in rows:
        if not isinstance(r, (list, tuple)):
            raise ValueError("table rows must be lists of cells")
        cells = list(r)[:n] + [None] * max(0, n - len(r))
        parts.append(row(cells, False))
    parts.append("\\pard\\par\n")
    return "".join(parts)


def build_rtf(doc: dict) -> bytes:
    """Serialise ``doc`` (the .docx document dict) to RTF bytes."""
    if not isinstance(doc, dict):
        raise ValueError("doc must be an object")
    blocks = doc.get("blocks") or []
    if not isinstance(blocks, list):
        raise ValueError("blocks must be a list")
    if len(blocks) > _MAX_BLOCKS:
        raise ValueError(f"too many blocks (> {_MAX_BLOCKS})")
    paper = doc.get("paper") or "A4"
    if paper not in _PAGES:
        raise ValueError("paper must be A4 or Letter")
    pw, ph = _PAGES[paper]
    landscape = bool(doc.get("landscape"))
    if landscape:
        pw, ph = ph, pw
    text_w = pw - 2 * _MARGIN
    out: List[str] = [
        "{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1",
        "{\\fonttbl{\\f0\\fswiss\\fcharset0 Calibri;}}",
        "{\\colortbl;\\red16\\green52\\blue77;\\red16\\green52\\blue77;"
        "\\red227\\green233\\blue240;\\red92\\green102\\blue114;}",
        "{\\stylesheet{\\s0 Normal;}"
        "{\\s1\\outlinelevel0\\b\\fs32 heading 1;}"
        "{\\s2\\outlinelevel1\\b\\fs26 heading 2;}"
        "{\\s3\\outlinelevel2\\b\\fs22 heading 3;}}",
        "{\\info{\\title " + rtf_escape(doc.get("title") or "Report")
        + "}}",
        f"\\paperw{pw}\\paperh{ph}\\margl{_MARGIN}\\margr{_MARGIN}"
        f"\\margt{_MARGIN}\\margb{_MARGIN}" + ("\\landscape" if landscape
                                               else ""),
        "\\fs20\n",
    ]
    out.append(_para(doc.get("title") or "Report", "\\sa120\\b\\fs52\\cf1"))
    if doc.get("subtitle"):
        out.append(_para(doc["subtitle"], "\\sa200\\fs26\\cf4"))
    if doc.get("meta"):
        out.append(_table({"columns": ["Item", "Value"],
                           "rows": [list(m)[:2] for m in doc["meta"]],
                           "align": ["l", "l"]}, text_w))
    if doc.get("toc", True):
        out.append(_para("Contents", _HEAD[1]))
        out.append("{\\pard\\plain{\\field{\\*\\fldinst TOC \\\\o \"1-3\" "
                   "\\\\h \\\\z \\\\u }{\\fldrslt {\\cf4 Right-click and "
                   "choose Update Field to build the table of contents.}}}"
                   "\\par}\n\\page\n")
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            raise ValueError(f"block {i} must be an object")
        kind = b.get("type")
        if kind == "heading":
            lvl = int(b.get("level") or 1)
            if lvl not in (1, 2, 3):
                raise ValueError(f"block {i}: heading level must be 1..3")
            out.append(_para(b.get("text", ""), _HEAD[lvl]))
        elif kind == "paragraph":
            st = "\\i\\fs18\\cf4" if b.get("style") == "note" else ""
            out.append(_para(b.get("text", ""), st))
        elif kind == "table":
            try:
                out.append(_table(b, text_w))
            except ValueError as exc:
                raise ValueError(f"block {i}: {exc}") from None
        elif kind == "image":
            try:
                data = _png_bytes(b)
                w, h = _png_size(data)
            except ValueError as exc:
                raise ValueError(f"block {i}: {exc}") from None
            dw = b.get("width_px")
            gw, gh = float(w), float(h)
            if dw is not None:
                try:
                    dw = float(dw)
                except (TypeError, ValueError):
                    raise ValueError(f"block {i}: width_px must be a "
                                     "number") from None
                if dw <= 0:
                    raise ValueError(f"block {i}: width_px must be > 0")
                gh, gw = gh * dw / gw, dw
            tw, th = gw * _TWIP_PX, gh * _TWIP_PX
            if tw > text_w:
                th, tw = th * text_w / tw, text_w
            hexd = binascii.hexlify(data).decode("ascii")
            lines = "\n".join(hexd[k:k + 128]
                              for k in range(0, len(hexd), 128))
            out.append("{\\pard\\plain\\qc\\keepn{\\pict\\pngblip"
                       f"\\picw{w}\\pich{h}\\picwgoal{int(round(tw))}"
                       f"\\pichgoal{int(round(th))}\n{lines}\n}}\\par}}\n")
            if b.get("caption"):
                out.append(_para(b["caption"], "\\qc\\i\\fs18"))
        elif kind == "page_break":
            out.append("\\page\n")
        else:
            raise ValueError(f"block {i}: unknown type {kind!r}")
    out.append("}")
    return "".join(out).encode("ascii")
