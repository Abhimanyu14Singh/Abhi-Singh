"""Tiny SVG sketcher for the benchmark report (redrawn from the published
geometry -- no CSI figure is reproduced).  Strokes use ``currentColor`` and
CSS classes so the sketch follows the report's light / dark theme."""

from __future__ import annotations

from html import escape
from typing import Iterable, List, Optional, Sequence, Tuple

P2 = Tuple[float, float]


class Sketch:
    """Collect primitives in model coordinates (x right, y up) and render
    them scaled into a ``w`` x ``h`` view box."""

    def __init__(self, w: int = 420, h: int = 260, pad: int = 28):
        self.w, self.h, self.pad = w, h, pad
        self.items: List[tuple] = []

    # primitives ------------------------------------------------------------
    def line(self, a: P2, b: P2, cls: str = "m") -> "Sketch":
        self.items.append(("line", a, b, cls))
        return self

    def poly(self, pts: Sequence[P2], cls: str = "wall") -> "Sketch":
        self.items.append(("poly", list(pts), cls))
        return self

    def dot(self, p: P2, cls: str = "pin") -> "Sketch":
        self.items.append(("dot", p, cls))
        return self

    def fixed(self, p: P2) -> "Sketch":
        self.items.append(("fixed", p))
        return self

    def arrow(self, p: P2, d: P2, label: str = "", cls: str = "ld"
              ) -> "Sketch":
        """Arrow ending at ``p`` pointing along unit direction ``d``."""
        self.items.append(("arrow", p, d, label, cls))
        return self

    def text(self, p: P2, s: str, cls: str = "lbl", anchor: str = "middle"
             ) -> "Sketch":
        self.items.append(("text", p, s, cls, anchor))
        return self

    # render ----------------------------------------------------------------
    def _pts(self) -> Iterable[P2]:
        for it in self.items:
            if it[0] == "line":
                yield it[1]
                yield it[2]
            elif it[0] == "poly":
                yield from it[1]
            elif it[0] in ("dot", "fixed", "text"):
                yield it[1]
            elif it[0] == "arrow":
                yield it[1]

    def render(self, title: str = "") -> str:
        pts = list(self._pts()) or [(0.0, 0.0)]
        x0 = min(p[0] for p in pts)
        x1 = max(p[0] for p in pts)
        y0 = min(p[1] for p in pts)
        y1 = max(p[1] for p in pts)
        sx = (self.w - 2 * self.pad) / max(x1 - x0, 1e-9)
        sy = (self.h - 2 * self.pad) / max(y1 - y0, 1e-9)
        s = min(sx, sy)
        ox = (self.w - s * (x1 - x0)) / 2.0
        oy = (self.h - s * (y1 - y0)) / 2.0

        def T(p: P2) -> P2:
            return (ox + (p[0] - x0) * s, self.h - (oy + (p[1] - y0) * s))

        out = [f'<svg class="sk" viewBox="0 0 {self.w} {self.h}" '
               f'role="img" aria-label="{escape(title)}">']
        for it in self.items:
            k = it[0]
            if k == "poly":
                d = " ".join(f"{T(p)[0]:.1f},{T(p)[1]:.1f}" for p in it[1])
                out.append(f'<polygon class="{it[2]}" points="{d}"/>')
        for it in self.items:
            k = it[0]
            if k == "line":
                a, b = T(it[1]), T(it[2])
                out.append(f'<line class="{it[3]}" x1="{a[0]:.1f}" '
                           f'y1="{a[1]:.1f}" x2="{b[0]:.1f}" '
                           f'y2="{b[1]:.1f}"/>')
            elif k == "fixed":
                p = T(it[1])
                out.append(f'<path class="sup" d="M{p[0] - 9:.1f} '
                           f'{p[1]:.1f}h18"/>')
                for i in range(4):
                    xx = p[0] - 8 + 5 * i
                    out.append(f'<path class="sup" d="M{xx:.1f} {p[1]:.1f}'
                               f'l-4 5"/>')
            elif k == "dot":
                p = T(it[1])
                out.append(f'<circle class="{it[2]}" cx="{p[0]:.1f}" '
                           f'cy="{p[1]:.1f}" r="3.2"/>')
            elif k == "arrow":
                p = T(it[1])
                dx, dy = it[2][0], -it[2][1]
                L = 26.0
                q = (p[0] - dx * L, p[1] - dy * L)
                hx, hy = -dx * 7, -dy * 7
                nx, ny = -dy * 4, dx * 4
                out.append(f'<line class="{it[4]}" x1="{q[0]:.1f}" '
                           f'y1="{q[1]:.1f}" x2="{p[0]:.1f}" '
                           f'y2="{p[1]:.1f}"/>')
                out.append(f'<path class="{it[4]}h" d="M{p[0]:.1f} '
                           f'{p[1]:.1f}L{p[0] + hx + nx:.1f} '
                           f'{p[1] + hy + ny:.1f}L{p[0] + hx - nx:.1f} '
                           f'{p[1] + hy - ny:.1f}Z"/>')
                if it[3]:
                    out.append(f'<text class="lbl" x="{q[0]:.1f}" '
                               f'y="{q[1] - 4:.1f}" text-anchor="middle">'
                               f'{escape(it[3])}</text>')
            elif k == "text":
                p = T(it[1])
                out.append(f'<text class="{it[3]}" x="{p[0]:.1f}" '
                           f'y="{p[1]:.1f}" text-anchor="{it[4]}">'
                           f'{escape(it[2])}</text>')
        out.append("</svg>")
        return "".join(out)


def elevation(cols: Sequence[float], levels: Sequence[float],
              beams: bool = True, fixed: bool = True) -> Sketch:
    """Regular plane-frame elevation."""
    sk = Sketch()
    z0 = 0.0
    for x in cols:
        sk.line((x, z0), (x, levels[-1]))
        if fixed:
            sk.fixed((x, z0))
    if beams:
        for z in levels:
            sk.line((cols[0], z), (cols[-1], z), "b")
    return sk


def plan(points: Sequence[P2], edges: Sequence[Tuple[int, int]],
         cm: Optional[P2] = None) -> Sketch:
    sk = Sketch()
    for i, j in edges:
        sk.line(points[i], points[j], "b")
    for p in points:
        sk.dot(p, "col")
    if cm is not None:
        sk.dot(cm, "cm")
        sk.text((cm[0], cm[1]), "CM", "lbl", "start")
    return sk
