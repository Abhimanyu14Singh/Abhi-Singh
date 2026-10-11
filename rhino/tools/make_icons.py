#!/usr/bin/env python3
"""Generate the 24x24 component icons embedded in SkyFrame.Grasshopper.

Simple flat glyphs drawn at 4x and downsampled (anti-aliased). Requires Pillow.
    python rhino/tools/make_icons.py
writes rhino/SkyFrame.Grasshopper/Icons/*.png (committed; only re-run to change them).
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 4                      # supersampling
N = 24 * S
BLUE = (36, 99, 165, 255)
ORANGE = (232, 121, 36, 255)
GREY = (90, 90, 90, 255)
RED = (200, 50, 50, 255)
GREEN = (60, 150, 90, 255)
OUT = Path(__file__).resolve().parent.parent / "SkyFrame.Grasshopper" / "Icons"


def canvas():
    img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def s(*v):
    return [x * S for x in v]


def line(d, pts, color, w=2):
    d.line(s(*pts), fill=color, width=w * S, joint="curve")


def dot(d, x, y, r, color):
    d.ellipse(s(x - r, y - r, x + r, y + r), fill=color)


def save(img, name):
    img.resize((24, 24), Image.LANCZOS).save(OUT / f"{name}.png", optimize=True)


def frame_glyph(d):
    # portal frame: two columns + beam + brace
    line(d, (5, 21, 5, 4), BLUE, 3)
    line(d, (19, 21, 19, 4), BLUE, 3)
    line(d, (4, 4, 20, 4), ORANGE, 3)
    line(d, (5, 21, 19, 4), GREY, 1)


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    img, d = canvas()                      # plugin icon: skyline
    d.rectangle(s(3, 9, 9, 21), fill=BLUE)
    d.rectangle(s(10, 3, 15, 21), fill=ORANGE)
    d.rectangle(s(16, 12, 21, 21), fill=GREY)
    save(img, "SkyFrame")

    img, d = canvas()                      # connect: plug + link
    dot(d, 7, 12, 4, BLUE)
    dot(d, 17, 12, 4, ORANGE)
    line(d, (7, 12, 17, 12), GREY, 2)
    save(img, "Connect")

    img, d = canvas()
    frame_glyph(d)
    save(img, "Frame")

    img, d = canvas()                      # shell: meshed quad in perspective
    d.polygon(s(3, 17, 12, 21, 21, 15, 12, 11), fill=(120, 160, 210, 255), outline=BLUE)
    line(d, (7.5, 19, 16.5, 13), BLUE, 1)
    line(d, (7.5, 14, 16.5, 18), BLUE, 1)
    line(d, (12, 3, 12, 11), ORANGE, 2)
    save(img, "Shell")

    img, d = canvas()                      # support: fixed base triangle + hatch
    line(d, (12, 3, 12, 11), BLUE, 3)
    d.polygon(s(12, 10, 5, 18, 19, 18), fill=RED)
    for x in (5, 9, 13, 17):
        line(d, (x, 21, x + 3, 18.5), GREY, 1)
    save(img, "Support")

    img, d = canvas()                      # joint: node with members
    line(d, (2, 12, 22, 12), BLUE, 2)
    line(d, (12, 2, 12, 22), BLUE, 2)
    dot(d, 12, 12, 4.5, ORANGE)
    save(img, "Joint")

    img, d = canvas()                      # layer objects: stacked layers
    for i, c in enumerate((GREY, BLUE, ORANGE)):
        y = 17 - i * 5
        d.polygon(s(3, y, 12, y + 4, 21, y, 12, y - 4), fill=c)
    save(img, "Layer")

    img, d = canvas()                      # send: arrow into building
    d.rectangle(s(14, 5, 21, 21), fill=BLUE)
    line(d, (2, 13, 11, 13), ORANGE, 3)
    d.polygon(s(9, 8, 14, 13, 9, 18), fill=ORANGE)
    save(img, "Send")

    img, d = canvas()                      # results: deflected beam over bars
    for x, h in ((4, 8), (9, 13), (14, 6), (19, 11)):
        d.rectangle(s(x - 1.5, 21 - h, x + 1.5, 21), fill=BLUE)
    d.arc(s(2, -2, 22, 10), 20, 160, fill=ORANGE, width=2 * S)
    save(img, "Results")


if __name__ == "__main__":
    main()
