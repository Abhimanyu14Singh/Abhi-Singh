"""Minimal Word (.docx, WordprocessingML / OOXML) writer — no dependencies
(zipfile + XML strings), for File > Create Report > Download .docx.

``build_docx(doc) -> bytes`` with::

    doc = {
      "title": "Model — Analysis Report",       # cover title (+ core.xml)
      "subtitle": "...",                         # optional cover line
      "meta": [["Date", "..."], ["Units", "kN-m"]],   # optional cover table
      "paper": "A4" | "Letter", "landscape": false,   # page setup
      "toc": true,                               # Word TOC field (updated
                                                 #  when Word opens the file)
      "blocks": [
        {"type": "heading", "text": "1. Model", "level": 1},       # 1..3
        {"type": "paragraph", "text": "...", "style": "note"?},
        {"type": "table", "columns": ["Story", "P [kN]"],
         "rows": [["Story1", 12.5], ...], "align": ["l", "r"]?,
         "caption": "..."?},
        {"type": "image", "png_b64": "<base64>" | "data_url": "data:...",
         "caption": "..."?, "width_px"?: 330},  # display width (aspect kept)
        {"type": "page_break"},
      ]}

Images must be PNG (the frontend rasterises SVG plots and 3D snapshots to
PNG); their pixel size is read from the IHDR chunk and scaled to the text
width.  Validation errors raise ``ValueError``.  The package holds
``[Content_Types].xml``, ``_rels/.rels``, ``docProps/core.xml|app.xml``,
``word/document.xml``, ``word/styles.xml``, ``word/settings.xml``,
``word/_rels/document.xml.rels`` and ``word/media/image<N>.png``.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import io
import struct
import zipfile
from typing import List, Tuple
from xml.sax.saxutils import escape

__all__ = ["build_docx", "MIME"]

MIME = ("application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document")
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_EMU_PX = 9525                        # EMU per pixel at 96 dpi
_PAGES = {"A4": (11906, 16838), "Letter": (12240, 15840)}   # twips
_MARGIN = 1020                        # 18 mm in twips
_MAX_BLOCKS = 20000
_MAX_IMAGE = 12 * 1024 * 1024


def _t(text) -> str:
    s = escape("" if text is None else str(text))
    sp = ' xml:space="preserve"' if s != s.strip() or "  " in s else ""
    return f"<w:t{sp}>{s}</w:t>"


def _run(text, bold=False, size=None, color=None) -> str:
    rpr = ""
    if bold or size or color:
        rpr = ("<w:rPr>" + ("<w:b/>" if bold else "")
               + (f'<w:color w:val="{color}"/>' if color else "")
               + (f'<w:sz w:val="{int(size)}"/>' if size else "")
               + "</w:rPr>")
    parts = str("" if text is None else text).split("\n")
    body = "<w:br/>".join(_t(p) for p in parts)
    return f"<w:r>{rpr}{body}</w:r>"


def _para(text="", style=None, align=None, bold=False, size=None,
          keep_next=False, runs=None) -> str:
    ppr = ""
    if style or align or keep_next:
        ppr = ("<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "")
               + ("<w:keepNext/>" if keep_next else "")
               + (f'<w:jc w:val="{align}"/>' if align else "") + "</w:pPr>")
    inner = runs if runs is not None else (
        _run(text, bold, size) if text != "" else "")
    return f"<w:p>{ppr}{inner}</w:p>"


def _png_size(data: bytes) -> Tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("image blocks must be PNG")
    w, h = struct.unpack(">II", data[16:24])
    if w <= 0 or h <= 0:
        raise ValueError("bad PNG size")
    return int(w), int(h)


def _png_bytes(b: dict) -> bytes:
    raw = b.get("png_b64")
    if raw is None:
        url = str(b.get("data_url") or "")
        if not url.startswith("data:image/png;base64,"):
            raise ValueError("image block needs png_b64 or a PNG data_url")
        raw = url.split(",", 1)[1]
    try:
        data = base64.b64decode(str(raw), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"image is not valid base64: {exc}") from None
    if len(data) > _MAX_IMAGE:
        raise ValueError("image larger than 12 MB")
    return data


def _drawing(rid: str, n: int, cx: int, cy: int, name: str) -> str:
    nm = escape(name, {'"': "&quot;"})
    return (
        "<w:r><w:drawing><wp:inline distT=\"0\" distB=\"0\" distL=\"0\" "
        f"distR=\"0\"><wp:extent cx=\"{cx}\" cy=\"{cy}\"/>"
        f"<wp:docPr id=\"{n}\" name=\"Picture {n}\" descr=\"{nm}\"/>"
        "<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect=\"1\"/>"
        "</wp:cNvGraphicFramePr><a:graphic><a:graphicData uri=\"http://"
        "schemas.openxmlformats.org/drawingml/2006/picture\"><pic:pic>"
        f"<pic:nvPicPr><pic:cNvPr id=\"{n}\" name=\"image{n}.png\"/>"
        "<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>"
        f"<a:blip r:embed=\"{rid}\"/><a:stretch><a:fillRect/></a:stretch>"
        "</pic:blipFill><pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/>"
        f"<a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm><a:prstGeom prst=\"rect\">"
        "<a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData>"
        "</a:graphic></wp:inline></w:drawing></w:r>")


def _cell(v, align: str, header: bool, width: int) -> str:
    if isinstance(v, float):
        txt = f"{v:.6g}"
    else:
        txt = "" if v is None else str(v)
    jc = "left" if align == "l" else "right"
    shade = ('<w:shd w:val="clear" w:color="auto" w:fill="EEF2F6"/>'
             if header else "")
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}'
            '</w:tcPr><w:p><w:pPr><w:pStyle w:val="TableText"/>'
            f'<w:jc w:val="{jc}"/></w:pPr>{_run(txt, bold=header)}</w:p>'
            "</w:tc>")


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
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for _ in cols)
    head = ('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>'
            + "".join(_cell(c, align[i], True, w) for i, c in enumerate(cols))
            + "</w:tr>")
    body = []
    for r in rows:
        if not isinstance(r, (list, tuple)):
            raise ValueError("table rows must be lists of cells")
        cells = list(r)[:n] + [None] * max(0, n - len(r))
        body.append('<w:tr><w:trPr><w:cantSplit/></w:trPr>' + "".join(
            _cell(v, align[i], False, w) for i, v in enumerate(cells))
            + "</w:tr>")
    out = ('<w:tbl><w:tblPr><w:tblStyle w:val="ReportTable"/>'
           '<w:tblW w:w="5000" w:type="pct"/><w:tblLayout w:type="autofit"/>'
           '</w:tblPr>' f"<w:tblGrid>{grid}</w:tblGrid>{head}"
           + "".join(body) + "</w:tbl>")
    cap = b.get("caption")
    return ((_para(cap, style="Caption", keep_next=True) if cap else "")
            + out + _para(""))


_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/><w:sz w:val="20"/><w:lang w:val="en-US"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:color w:val="10344D"/><w:sz w:val="52"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:color w:val="5C6672"/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/><w:pBdr><w:bottom w:val="single" w:sz="8" w:space="2" w:color="1274AB"/></w:pBdr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="10344D"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="38424D"/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:color w:val="38424D"/><w:sz w:val="21"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Note"><w:name w:val="Note"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="5C6672"/><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:i/><w:color w:val="5C6672"/><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="16"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TOC1"><w:name w:val="toc 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/></w:style>
<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
<w:style w:type="table" w:styleId="ReportTable"><w:name w:val="Report Table"/><w:basedOn w:val="TableNormal"/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:left w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:right w:val="single" w:sz="4" w:space="0" w:color="D0D8E0"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="E1E6EC"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="E1E6EC"/></w:tblBorders><w:tblCellMar><w:left w:w="60" w:type="dxa"/><w:right w:w="60" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>
</w:styles>"""


def build_docx(doc: dict) -> bytes:
    """Serialise ``doc`` (see module docstring) to .docx bytes."""
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
    max_cx = text_w * 635                       # twip -> EMU (1 twip = 635)
    title = str(doc.get("title") or "Report")
    body: List[str] = [_para(title, style="Title")]
    if doc.get("subtitle"):
        body.append(_para(doc["subtitle"], style="Subtitle"))
    if doc.get("meta"):
        body.append(_table({"columns": ["Item", "Value"],
                            "rows": [list(m)[:2] for m in doc["meta"]],
                            "align": ["l", "l"]}, text_w))
    if doc.get("toc", True):
        body.append(_para("Contents", style="Heading1"))
        body.append(
            '<w:p><w:r><w:fldChar w:fldCharType="begin" w:dirty="true"/>'
            '</w:r><w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" '
            '\\h \\z \\u </w:instrText></w:r><w:r><w:fldChar '
            'w:fldCharType="separate"/></w:r>'
            + _run("Right-click and choose Update Field to build the "
                   "table of contents.", color="5C6672")
            + '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
    media: List[bytes] = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            raise ValueError(f"block {i} must be an object")
        kind = b.get("type")
        if kind == "heading":
            lvl = int(b.get("level") or 1)
            if lvl not in (1, 2, 3):
                raise ValueError(f"block {i}: heading level must be 1..3")
            body.append(_para(b.get("text", ""), style=f"Heading{lvl}"))
        elif kind == "paragraph":
            st = "Note" if b.get("style") == "note" else None
            body.append(_para(b.get("text", ""), style=st))
        elif kind == "table":
            try:
                body.append(_table(b, text_w))
            except ValueError as exc:
                raise ValueError(f"block {i}: {exc}") from None
        elif kind == "image":
            try:
                data = _png_bytes(b)
                w, h = _png_size(data)
            except ValueError as exc:
                raise ValueError(f"block {i}: {exc}") from None
            media.append(data)
            n = len(media)
            dw = b.get("width_px")             # optional display width
            if dw is not None:
                try:
                    dw = float(dw)
                except (TypeError, ValueError):
                    raise ValueError(f"block {i}: width_px must be a "
                                     "number") from None
                if dw <= 0:
                    raise ValueError(f"block {i}: width_px must be > 0")
                h, w = h * dw / w, dw
            cx, cy = int(w * _EMU_PX), int(h * _EMU_PX)
            if cx > max_cx:
                cy = int(cy * max_cx / cx)
                cx = max_cx
            body.append('<w:p><w:pPr><w:keepNext/><w:jc w:val="center"/>'
                        "</w:pPr>" + _drawing(f"rIdImg{n}", n, cx, cy,
                                              b.get("caption") or "")
                        + "</w:p>")
            if b.get("caption"):
                body.append(_para(b["caption"], style="Caption",
                                  align="center"))
        elif kind == "page_break":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        else:
            raise ValueError(f"block {i}: unknown type {kind!r}")
    orient = ' w:orient="landscape"' if landscape else ""
    sect = (f'<w:sectPr><w:pgSz w:w="{pw}" w:h="{ph}"{orient}/>'
            f'<w:pgMar w:top="{_MARGIN}" w:right="{_MARGIN}" '
            f'w:bottom="{_MARGIN}" w:left="{_MARGIN}" w:header="567" '
            'w:footer="567" w:gutter="0"/></w:sectPr>')
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:r="{_R}" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
        'wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/'
        'drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/'
        'drawingml/2006/picture"><w:body>' + "".join(body) + sect
        + "</w:body></w:document>")
    rels = ([f'<Relationship Id="rIdStyles" Type="{_R}/styles" '
             'Target="styles.xml"/>',
             f'<Relationship Id="rIdSettings" Type="{_R}/settings" '
             'Target="settings.xml"/>']
            + [f'<Relationship Id="rIdImg{k + 1}" Type="{_R}/image" '
               f'Target="media/image{k + 1}.png"/>'
               for k in range(len(media))])
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="'
            'application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            '<Override PartName="/word/document.xml" ContentType="'
            f'{MIME}.main+xml"/><Override PartName="/word/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.styles+xml"/><Override PartName="/word/'
            'settings.xml" ContentType="application/vnd.openxmlformats-'
            'officedocument.wordprocessingml.settings+xml"/><Override '
            'PartName="/docProps/core.xml" ContentType="application/vnd.'
            'openxmlformats-package.core-properties+xml"/><Override '
            'PartName="/docProps/app.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.extended-properties+xml"/>'
            '</Types>'))
        z.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_PKG}">'
            f'<Relationship Id="rId1" Type="{_R}/officeDocument" '
            'Target="word/document.xml"/><Relationship Id="rId2" Type="http:'
            '//schemas.openxmlformats.org/package/2006/relationships/'
            'metadata/core-properties" Target="docProps/core.xml"/>'
            f'<Relationship Id="rId3" Type="{_R}/extended-properties" '
            'Target="docProps/app.xml"/></Relationships>'))
        z.writestr("docProps/core.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
            'package/2006/metadata/core-properties" xmlns:dc="http://purl.'
            'org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(title)}</dc:title><dc:creator>SkyFrame"
            "</dc:creator><dcterms:created xsi:type=\"dcterms:W3CDTF\">"
            f"{now}</dcterms:created></cp:coreProperties>"))
        z.writestr("docProps/app.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/'
            'officeDocument/2006/extended-properties"><Application>SkyFrame'
            "</Application></Properties>"))
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("word/settings.xml", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<w:settings xmlns:w="{_W}"><w:updateFields w:val="true"/>'
            '<w:defaultTabStop w:val="720"/></w:settings>'))
        z.writestr("word/_rels/document.xml.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_PKG}">' + "".join(rels)
            + "</Relationships>"))
        for k, data in enumerate(media):
            z.writestr(f"word/media/image{k + 1}.png", data)
    return buf.getvalue()
