"""Validation of the built-in steel section tables (v1.21 data files).

Covers ``skyframe/data/steel/{eurocode,is808,bs,as_nzs}.json``:

* schema of every record (spec "Steel section database files"),
* spot checks of >= 15 designations per standard against the published
  values, quoted below in the units the source prints them,
* internal consistency: A ~= mass / 7850, I33 > I22 for I-shapes,
  r = sqrt(I/A), closed-form area from the published dimensions, angle
  principal-axis invariants, CHS J = 2I, S <= Z.

The tests read the JSON with a minimal local reader on purpose: the public
loader (``skyframe.core.steel_db``) is owned by another module.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "skyframe" / "data" / "steel"
STANDARDS = ("eurocode", "is808", "bs", "as_nzs")
TYPES = {"I", "I_mono", "channel", "angle", "double_angle", "tee", "rect_tube",
         "round_tube", "rect_solid", "round_solid", "plate"}
REQUIRED = ("A", "I33", "I22", "J", "Cw", "S33", "S22", "Z33", "Z22", "As2",
            "As3", "r33", "r22", "x0", "y0", "mass")
UNIT = {"mm2": 1e-6, "cm2": 1e-4, "mm3": 1e-9, "cm3": 1e-6, "1e3mm3": 1e-6,
        "mm4": 1e-12, "cm4": 1e-8, "1e3mm4": 1e-9, "1e6mm4": 1e-6,
        "mm6": 1e-18, "cm6": 1e-12, "dm6": 1e-6, "1e9mm6": 1e-9, "kg/m": 1.0}


def _load(std):
    with open(DATA / f"{std}.json", encoding="utf-8") as f:
        return json.load(f)


_CACHE = {}


def table(std):
    if std not in _CACHE:
        doc = _load(std)
        _CACHE[std] = (doc, {s["name"]: s for s in doc["shapes"]})
    return _CACHE[std]


# --------------------------------------------------------------------------
# Published values (units as printed by the source).  Sources, per standard:
#   eurocode: EN 10365 tables (eurocodedesign), ArcelorMittal Orange Book,
#             ArcelorMittal UPE table (structuralcodes), EN 10056-1 angles,
#             EN 10210-2 hot-finished (eurocodepy), EN 10219-2 (SCI Blue Book)
#   bs:       SCI P363 Blue Book (Eurocode edition)
#   is808:    IS 808:2021 / IS 4923:1997 / IS 1161:2014 (Osdag database)
#   as_nzs:   ASI / InfraBuild tables (AS/NZS 3679.1, 3679.2, 1163)
# See skyframe/docs/SECTION_SOURCES.md for URLs, commits and editions.
# --------------------------------------------------------------------------
SPOT = {
    "eurocode": [
        ('IPE300', {'A': (5381, 'mm2'), 'I33': (8.356e+07, 'mm4'), 'I22': (6.038e+06, 'mm4'), 'Z33': (628400, 'mm3'), 'J': (197500, 'mm4'), 'Cw': (1.24e+11, 'mm6'), 'mass': (42.2, 'kg/m')}),
        ('IPE600', {'A': (15598, 'mm2'), 'I33': (9.208e+08, 'mm4'), 'I22': (3.387e+07, 'mm4'), 'Z33': (3.512e+06, 'mm3'), 'J': (1.646e+06, 'mm4'), 'Cw': (2.81e+12, 'mm6'), 'mass': (122.4, 'kg/m')}),
        ('HEA200', {'A': (5383, 'mm2'), 'I33': (3.692e+07, 'mm4'), 'I22': (1.336e+07, 'mm4'), 'Z33': (429500, 'mm3'), 'J': (204300, 'mm4'), 'Cw': (1.06e+11, 'mm6'), 'mass': (42.3, 'kg/m')}),
        ('HEB300', {'A': (14908, 'mm2'), 'I33': (2.517e+08, 'mm4'), 'I22': (8.563e+07, 'mm4'), 'Z33': (1.869e+06, 'mm3'), 'J': (1.874e+06, 'mm4'), 'Cw': (1.65e+12, 'mm6'), 'mass': (117, 'kg/m')}),
        ('HEM400', {'A': (32578, 'mm2'), 'I33': (1.041e+09, 'mm4'), 'I22': (1.934e+08, 'mm4'), 'Z33': (5.571e+06, 'mm3'), 'J': (1.524e+07, 'mm4'), 'Cw': (7.27e+12, 'mm6'), 'mass': (255.7, 'kg/m')}),
        ('IPEA300', {'A': (46.5, 'cm2'), 'I33': (7170, 'cm4'), 'I22': (519, 'cm4'), 'Z33': (542, 'cm3'), 'J': (13.3, 'cm4'), 'mass': (36.5, 'kg/m')}),
        ('IPEO400', {'A': (96.4, 'cm2'), 'I33': (26800, 'cm4'), 'I22': (1560, 'cm4'), 'Z33': (1500, 'cm3'), 'J': (73.3, 'cm4'), 'mass': (75.7, 'kg/m')}),
        ('IPE750x147', {'A': (188, 'cm2'), 'I33': (166000, 'cm4'), 'I22': (5290, 'cm4'), 'Z33': (5110, 'cm3'), 'J': (157, 'cm4'), 'mass': (147, 'kg/m')}),
        ('HEAA300', {'A': (88.9, 'cm2'), 'I33': (13800, 'cm4'), 'I22': (4730, 'cm4'), 'Z33': (1060, 'cm3'), 'J': (47.8, 'cm4'), 'mass': (69.8, 'kg/m')}),
        ('UPN200', {'A': (32.2, 'cm2'), 'I33': (1910, 'cm4'), 'I22': (148, 'cm4'), 'Z33': (228, 'cm3'), 'J': (11.9, 'cm4'), 'mass': (25.3, 'kg/m')}),
        ('UPN300', {'A': (58.8, 'cm2'), 'I33': (8030, 'cm4'), 'I22': (495, 'cm4'), 'Z33': (632, 'cm3'), 'J': (37.4, 'cm4'), 'mass': (46.2, 'kg/m')}),
        ('UPE200', {'A': (29, 'cm2'), 'I33': (1909, 'cm4'), 'I22': (187.3, 'cm4'), 'J': (8.89, 'cm4'), 'Cw': (11000, 'cm6'), 'mass': (22.8, 'kg/m')}),
        ('L100x100x10', {'A': (19.2, 'cm2'), 'I33': (177, 'cm4'), 'I22': (177, 'cm4'), 'I_major': (280, 'cm4'), 'I_minor': (73, 'cm4'), 'J': (6.97, 'cm4'), 'mass': (15, 'kg/m')}),
        ('L150x90x10', {'A': (23.2, 'cm2'), 'I33': (533, 'cm4'), 'I22': (146, 'cm4'), 'I_major': (591, 'cm4'), 'I_minor': (88.3, 'cm4'), 'J': (8.3, 'cm4'), 'mass': (18.2, 'kg/m')}),
        ('L200x200x16', {'A': (6180, 'mm2'), 'I33': (2.34e+07, 'mm4'), 'I_major': (3.72e+07, 'mm4'), 'I_minor': (9.6e+06, 'mm4'), 'mass': (48.5, 'kg/m')}),
        ('L200x150x12', {'A': (4080, 'mm2'), 'I33': (1.65e+07, 'mm4'), 'I_major': (2.03e+07, 'mm4'), 'I_minor': (4.3e+06, 'mm4'), 'mass': (32, 'kg/m')}),
        ('CHS168.3x10', {'A': (49.7, 'cm2'), 'I33': (1564, 'cm4'), 'J': (3128, 'cm4'), 'Z33': (251, 'cm3'), 'mass': (39, 'kg/m')}),
        ('RHS200x100x8', {'A': (44.8, 'cm2'), 'I33': (2234, 'cm4'), 'J': (1804, 'cm4'), 'Z33': (282, 'cm3'), 'mass': (35.1, 'kg/m')}),
        ('SHS100x100x5', {'A': (18.7, 'cm2'), 'I33': (279, 'cm4'), 'J': (439, 'cm4'), 'Z33': (66.4, 'cm3'), 'mass': (14.7, 'kg/m')}),
        ('CFRHS200x100x8', {'A': (43.2, 'cm2'), 'I33': (2090, 'cm4'), 'J': (1810, 'cm4'), 'mass': (33.9, 'kg/m')}),
    ],
    "bs": [
        ('UB 305x165x40', {'A': (51.3, 'cm2'), 'I33': (8500, 'cm4'), 'I22': (764, 'cm4'), 'Z33': (623, 'cm3'), 'J': (14.7, 'cm4'), 'Cw': (0.164, 'dm6'), 'mass': (40.3, 'kg/m')}),
        ('UB 533x210x92', {'A': (117, 'cm2'), 'I33': (55200, 'cm4'), 'I22': (2390, 'cm4'), 'Z33': (2360, 'cm3'), 'J': (75.7, 'cm4'), 'Cw': (1.6, 'dm6'), 'mass': (92.1, 'kg/m')}),
        ('UB 914x419x388', {'A': (494, 'cm2'), 'I33': (720000, 'cm4'), 'I22': (45400, 'cm4'), 'Z33': (17700, 'cm3'), 'J': (1730, 'cm4'), 'Cw': (88.9, 'dm6'), 'mass': (388, 'kg/m')}),
        ('UC 203x203x46', {'A': (58.7, 'cm2'), 'I33': (4570, 'cm4'), 'I22': (1550, 'cm4'), 'Z33': (497, 'cm3'), 'J': (22.2, 'cm4'), 'Cw': (0.143, 'dm6'), 'mass': (46.1, 'kg/m')}),
        ('UC 305x305x97', {'A': (123, 'cm2'), 'I33': (22200, 'cm4'), 'I22': (7310, 'cm4'), 'Z33': (1590, 'cm3'), 'J': (91.2, 'cm4'), 'Cw': (1.56, 'dm6'), 'mass': (96.9, 'kg/m')}),
        ('UBP 305x305x110', {'A': (140, 'cm2'), 'I33': (23600, 'cm4'), 'I22': (7710, 'cm4'), 'Z33': (1720, 'cm3'), 'J': (122, 'cm4'), 'Cw': (1.65, 'dm6'), 'mass': (110, 'kg/m')}),
        ('PFC 300x100x46', {'A': (58, 'cm2'), 'I33': (8230, 'cm4'), 'I22': (568, 'cm4'), 'Z33': (641, 'cm3'), 'J': (36.8, 'cm4'), 'Cw': (0.0813, 'dm6'), 'mass': (45.5, 'kg/m')}),
        ('L 100x100x10', {'A': (19.2, 'cm2'), 'I33': (177, 'cm4'), 'I_major': (280, 'cm4'), 'I_minor': (73, 'cm4'), 'J': (6.97, 'cm4'), 'mass': (15, 'kg/m')}),
        ('L 150x90x10', {'A': (23.2, 'cm2'), 'I33': (533, 'cm4'), 'I22': (146, 'cm4'), 'I_minor': (88.3, 'cm4'), 'J': (8.3, 'cm4'), 'mass': (18.2, 'kg/m')}),
        ('TUB 254x343x63', {'A': (79.7, 'cm2'), 'I33': (8980, 'cm4'), 'I22': (2190, 'cm4'), 'J': (57.9, 'cm4'), 'mass': (62.6, 'kg/m')}),
        ('TUC 305x152x79', {'I33': (1530, 'cm4'), 'I22': (6280, 'cm4'), 'Z33': (225, 'cm3'), 'mass': (79, 'kg/m')}),
        ('CHS 168.3x10.0', {'A': (49.7, 'cm2'), 'I33': (1560, 'cm4'), 'J': (3130, 'cm4'), 'mass': (39, 'kg/m')}),
        ('RHS 200x100x8.0', {'A': (44.8, 'cm2'), 'I33': (2230, 'cm4'), 'J': (1800, 'cm4'), 'mass': (35.1, 'kg/m')}),
        ('SHS 100x100x5.0', {'A': (18.7, 'cm2'), 'I33': (279, 'cm4'), 'J': (439, 'cm4'), 'mass': (14.7, 'kg/m')}),
        ('CFCHS 168.3x8.0', {'A': (40.3, 'cm2'), 'I33': (1300, 'cm4'), 'J': (2600, 'cm4'), 'mass': (31.6, 'kg/m')}),
        ('CFRHS 200x100x6.0', {'A': (33.6, 'cm2'), 'I33': (1700, 'cm4'), 'J': (1420, 'cm4'), 'mass': (26.4, 'kg/m')}),
        ('CFSHS 100x100x5.0', {'A': (18.4, 'cm2'), 'I33': (271, 'cm4'), 'J': (441, 'cm4'), 'mass': (14.4, 'kg/m')}),
    ],
    "is808": [
        ('MB 300', {'A': (58.6, 'cm2'), 'I33': (8990, 'cm4'), 'I22': (486, 'cm4'), 'Z33': (681, 'cm3'), 'J': (34.7, 'cm4'), 'Cw': (123000, 'cm6'), 'mass': (46.02, 'kg/m')}),
        ('MB 600', {'A': (154, 'cm2'), 'I33': (90200, 'cm4'), 'I22': (2570, 'cm4'), 'Z33': (3450, 'cm3'), 'J': (198, 'cm4'), 'Cw': (2.63e+06, 'cm6'), 'mass': (121, 'kg/m')}),
        ('WB 600 (145.06 kg/m)', {'A': (184, 'cm2'), 'I33': (115000, 'cm4'), 'I22': (5290, 'cm4'), 'Z33': (4340, 'cm3'), 'J': (305, 'cm4'), 'Cw': (5.1e+06, 'cm6'), 'mass': (145.06, 'kg/m')}),
        ('HB 300', {'A': (74.8, 'cm2'), 'I33': (12500, 'cm4'), 'I22': (2190, 'cm4'), 'Z33': (921, 'cm3'), 'J': (32.4, 'cm4'), 'Cw': (577000, 'cm6'), 'mass': (58.74, 'kg/m')}),
        ('LB 300', {'A': (48, 'cm2'), 'I33': (7340, 'cm4'), 'I22': (376, 'cm4'), 'Z33': (554, 'cm3'), 'J': (18.1, 'cm4'), 'Cw': (111000, 'cm6'), 'mass': (37.72, 'kg/m')}),
        ('JB 200', {'A': (12.6, 'cm2'), 'I33': (780, 'cm4'), 'I22': (17.2, 'cm4'), 'Z33': (90.9, 'cm3'), 'J': (0.873, 'cm4'), 'Cw': (1710, 'cm6'), 'mass': (9.92, 'kg/m')}),
        ('SC 200', {'A': (76.7, 'cm2'), 'I33': (5520, 'cm4'), 'I22': (1520, 'cm4'), 'Z33': (627, 'cm3'), 'J': (74.9, 'cm4'), 'Cw': (171000, 'cm6'), 'mass': (60.24, 'kg/m')}),
        ('NPB 300x150x42.24', {'A': (53.8, 'cm2'), 'I33': (8350, 'cm4'), 'I22': (603, 'cm4'), 'Z33': (628, 'cm3'), 'J': (19.9, 'cm4'), 'Cw': (125000, 'cm6'), 'mass': (42.24, 'kg/m')}),
        ('WPB 300x300x88.34', {'A': (112, 'cm2'), 'I33': (18200, 'cm4'), 'I22': (6300, 'cm4'), 'Z33': (1380, 'cm3'), 'J': (87.8, 'cm4'), 'Cw': (1.19e+06, 'cm6'), 'mass': (88.34, 'kg/m')}),
        ('MC 300', {'A': (46.2, 'cm2'), 'I33': (6400, 'cm4'), 'I22': (311, 'cm4'), 'Z33': (501, 'cm3'), 'J': (21.7, 'cm4'), 'mass': (36.3, 'kg/m')}),
        ('MPC 300', {'A': (46.2, 'cm2'), 'I33': (6420, 'cm4'), 'I22': (351, 'cm4'), 'Z33': (502, 'cm3'), 'J': (19.8, 'cm4'), 'mass': (36.3, 'kg/m')}),
        ('LC 300', {'A': (42.1, 'cm2'), 'I33': (6050, 'cm4'), 'I22': (344, 'cm4'), 'Z33': (467, 'cm3'), 'J': (15.6, 'cm4'), 'mass': (33.1, 'kg/m')}),
        ('JC 200', {'A': (17.7, 'cm2'), 'I33': (1160, 'cm4'), 'I22': (82.8, 'cm4'), 'Z33': (133, 'cm3'), 'J': (2.23, 'cm4'), 'mass': (14, 'kg/m')}),
        ('ISA 100x100x10', {'A': (19.1, 'cm2'), 'I33': (180, 'cm4'), 'I22': (180, 'cm4'), 'I_major': (286, 'cm4'), 'I_minor': (74.3, 'cm4'), 'J': (6.33, 'cm4'), 'mass': (15.04, 'kg/m')}),
        ('ISA 100x75x8', {'A': (13.5, 'cm2'), 'I33': (135, 'cm4'), 'I22': (65.7, 'cm4'), 'I_major': (165, 'cm4'), 'I_minor': (35.5, 'cm4'), 'J': (2.85, 'cm4'), 'mass': (10.61, 'kg/m')}),
        ('SHS 72x72x4.8', {'A': (12.31, 'cm2'), 'I33': (90.31, 'cm4'), 'Z33': (30.49, 'cm3'), 'mass': (9.66, 'kg/m')}),
        ('RHS 122x61x4.5', {'A': (15.14, 'cm2'), 'I33': (278.94, 'cm4'), 'I22': (93.78, 'cm4'), 'mass': (11.88, 'kg/m')}),
        ('CHS 168.3x4.5', {'A': (23.16, 'cm2'), 'I33': (777.22, 'cm4'), 'S33': (92.36, 'cm3'), 'mass': (18.18, 'kg/m')}),
    ],
    "as_nzs": [
        ('310UB40.4', {'A': (5210, 'mm2'), 'I33': (86.4, '1e6mm4'), 'I22': (7.65, '1e6mm4'), 'Z33': (633, '1e3mm3'), 'J': (157, '1e3mm4'), 'Cw': (165, '1e9mm6'), 'mass': (40.4, 'kg/m')}),
        ('610UB125', {'A': (16000, 'mm2'), 'I33': (986, '1e6mm4'), 'I22': (39.3, '1e6mm4'), 'Z33': (3680, '1e3mm3'), 'J': (1560, '1e3mm4'), 'Cw': (3450, '1e9mm6'), 'mass': (125, 'kg/m')}),
        ('760UB244', {'A': (31100, 'mm2'), 'I33': (3020, '1e6mm4'), 'I22': (105, '1e6mm4'), 'Z33': (8940, '1e3mm3'), 'J': (7530, '1e3mm4'), 'Cw': (14800, '1e9mm6'), 'mass': (244, 'kg/m')}),
        ('310UC158', {'A': (20100, 'mm2'), 'I33': (388, '1e6mm4'), 'I22': (125, '1e6mm4'), 'Z33': (2680, '1e3mm3'), 'J': (3810, '1e3mm4'), 'Cw': (2860, '1e9mm6'), 'mass': (158, 'kg/m')}),
        ('100UC14.8', {'A': (1890, 'mm2'), 'I33': (3.18, '1e6mm4'), 'I22': (1.14, '1e6mm4'), 'Z33': (74.4, '1e3mm3'), 'J': (34.9, '1e3mm4'), 'Cw': (2.3, '1e9mm6'), 'mass': (14.8, 'kg/m')}),
        ('1200WB455', {'A': (57900, 'mm2'), 'I33': (15300, '1e6mm4'), 'I22': (834, '1e6mm4'), 'Z33': (28200, '1e3mm3'), 'J': (22000, '1e3mm4'), 'Cw': (280000, '1e9mm6'), 'mass': (455, 'kg/m')}),
        ('500WC440', {'A': (56000, 'mm2'), 'I33': (2150, '1e6mm4'), 'I22': (835, '1e6mm4'), 'Z33': (10400, '1e3mm3'), 'J': (30100, '1e3mm4'), 'Cw': (40400, '1e9mm6'), 'mass': (440, 'kg/m')}),
        ('380PFC', {'A': (7030, 'mm2'), 'I33': (152, '1e6mm4'), 'I22': (6.48, '1e6mm4'), 'J': (491, '1e3mm4'), 'Cw': (152, '1e9mm6'), 'mass': (55.2, 'kg/m')}),
        ('125TFB', {'A': (1670, 'mm2'), 'I33': (4.34, '1e6mm4'), 'I22': (0.337, '1e6mm4'), 'J': (40.2, '1e3mm4'), 'mass': (13.1, 'kg/m')}),
        ('100x100x10EA', {'A': (1810, 'mm2'), 'I33': (1.7, '1e6mm4'), 'I22': (1.7, '1e6mm4'), 'I_major': (2.7, '1e6mm4'), 'I_minor': (0.695, '1e6mm4'), 'J': (56.2, '1e3mm4'), 'mass': (14.2, 'kg/m')}),
        ('150x100x12UA', {'A': (2870, 'mm2'), 'I33': (6.52, '1e6mm4'), 'I22': (2.34, '1e6mm4'), 'I_major': (7.51, '1e6mm4'), 'I_minor': (1.35, '1e6mm4'), 'J': (141, '1e3mm4'), 'mass': (22.5, 'kg/m')}),
        ('200x100x6.0RHS', {'A': (3330, 'mm2'), 'I33': (16.7, '1e6mm4'), 'Z33': (210, '1e3mm3'), 'J': (14200, '1e3mm4'), 'mass': (26.2, 'kg/m')}),
        ('100x100x6.0SHS', {'A': (2130, 'mm2'), 'I33': (3.04, '1e6mm4'), 'Z33': (73.5, '1e3mm3'), 'J': (5150, '1e3mm4'), 'mass': (16.7, 'kg/m')}),
        ('168.3x7.1CHS', {'A': (3600, 'mm2'), 'I33': (11.7, '1e6mm4'), 'Z33': (185, '1e3mm3'), 'J': (23400, '1e3mm4'), 'mass': (28.2, 'kg/m')}),
        ('508.0x16.0CHS', {'A': (24700, 'mm2'), 'I33': (749, '1e6mm4'), 'Z33': (3870, '1e3mm3'), 'J': (1.5e+06, '1e3mm4'), 'mass': (194, 'kg/m')}),
    ],
}

# Family coverage required by the v1.21 request (per standard).
FAMILIES = {
    "eurocode": {"IPE", "IPEA", "IPEO", "HEA", "HEB", "HEM", "UPN", "UPE", "L equal",
                 "L unequal", "CHS", "RHS", "SHS", "CFCHS", "CFRHS", "CFSHS"},
    "is808": {"ISMB", "ISWB", "ISHB", "ISLB", "ISJB", "ISMC", "ISLC", "ISJC",
              "ISA equal", "ISA unequal", "SHS", "RHS", "CHS"},
    "bs": {"UB", "UC", "UBP", "PFC", "L equal", "L unequal", "CHS", "RHS", "SHS",
           "CFCHS", "CFRHS", "CFSHS", "TUB", "TUC"},
    "as_nzs": {"UB", "UC", "WB", "WC", "PFC", "TFB", "EA", "UA", "CHS", "RHS", "SHS"},
}


def _shapes(std):
    return table(std)[0]["shapes"]


def _close(a, b, rel):
    return abs(a - b) <= rel * abs(b)


# ------------------------------------------------------------------ schema
@pytest.mark.parametrize("std", STANDARDS)
def test_file_header_and_unique_names(std):
    doc, by_name = table(std)
    for key in ("standard", "edition", "source", "units", "shapes"):
        assert doc[key], key
    assert doc["units"] == "SI (m, m^2, m^4, m^6, kg/m)"
    assert len(by_name) == len(doc["shapes"])           # names unique
    for s in doc["shapes"]:
        assert s["src"] in doc["sources"], s["name"]


@pytest.mark.parametrize("std", STANDARDS)
def test_family_coverage(std):
    fams = {s["family"] for s in _shapes(std)}
    assert FAMILIES[std] <= fams, FAMILIES[std] - fams


@pytest.mark.parametrize("std", STANDARDS)
def test_record_schema(std):
    for s in _shapes(std):
        assert s["type"] in TYPES, s["name"]
        assert isinstance(s["family"], str) and s["family"]
        for k, v in s["dims"].items():
            assert math.isfinite(v) and v >= 0, (s["name"], k)
        p = s["props"]
        for k in REQUIRED:
            assert k in p and math.isfinite(p[k]), (s["name"], k)
        if s["type"] == "angle":
            for k in ("I_major", "I_minor", "alpha"):
                assert k in p, (s["name"], k)
        for k in ("A", "I33", "I22", "J", "S33", "S22", "Z33", "Z22", "As2",
                  "As3", "r33", "r22", "mass"):
            assert p[k] > 0, (s["name"], k)
        assert p["Cw"] >= 0, s["name"]
        assert set(s.get("computed", [])) <= set(p), s["name"]
        # SI sanity: section sizes between 10 mm and 1.3 m
        size = s["dims"].get("d", s["dims"].get("D"))
        assert 0.01 <= size <= 1.3, (s["name"], size)


# ------------------------------------------------------------- spot checks
@pytest.mark.parametrize("std", STANDARDS)
def test_spot_checks_against_published_values(std):
    _, by_name = table(std)
    assert len(SPOT[std]) >= 15
    for name, vals in SPOT[std]:
        s = by_name[name]
        for key, (value, unit) in vals.items():
            assert key not in s.get("computed", []), (name, key)
            got = s["props"][key]
            want = value * UNIT[unit]
            assert _close(got, want, 5e-4), (name, key, got, want)


# ------------------------------------------------------------ consistency
@pytest.mark.parametrize("std", STANDARDS)
def test_area_matches_mass(std):
    """A ~= mass / 7850 (published masses are rounded; worst case IPE V 450,
    printed 107 kg/m for A = 132 cm^2)."""
    for s in _shapes(std):
        p = s["props"]
        assert _close(p["A"] * 7850.0, p["mass"], 0.035), (s["name"], p["A"] * 7850, p["mass"])


@pytest.mark.parametrize("std", STANDARDS)
def test_I_shapes_major_axis(std):
    n = 0
    for s in _shapes(std):
        if s["type"] == "I":
            n += 1
            p = s["props"]
            assert p["I33"] > p["I22"], s["name"]
            assert p["Z33"] > p["Z22"], s["name"]
            assert p["x0"] == 0.0 and p["y0"] == 0.0
    assert n > 0


@pytest.mark.parametrize("std", STANDARDS)
def test_radius_of_gyration_and_moduli(std):
    for s in _shapes(std):
        p = s["props"]
        assert _close(p["r33"], math.sqrt(p["I33"] / p["A"]), 0.02), s["name"]
        assert _close(p["r22"], math.sqrt(p["I22"] / p["A"]), 0.02), s["name"]
        # plastic >= elastic modulus (shape factor >= 1)
        assert p["Z33"] >= 0.99 * p["S33"], s["name"]
        assert p["Z22"] >= 0.99 * p["S22"], s["name"]


@pytest.mark.parametrize("std", STANDARDS)
def test_area_from_published_dimensions(std):
    """Closed-form areas from the printed dimensions (root/toe/corner radii
    included) reproduce the tabulated A."""
    n = 0
    for s in _shapes(std):
        d, p, t = s["dims"], s["props"], s["type"]
        if t == "I" and "flange_slope_deg" not in d and "r2" not in d:
            a = 2 * d["bf"] * d["tf"] + (d["d"] - 2 * d["tf"]) * d["tw"] + (4 - math.pi) * d["r"] ** 2
        elif t == "rect_tube":
            a = (2 * d["t"] * (d["b"] + d["d"] - 2 * d["t"])
                 - (4 - math.pi) * (d["r_out"] ** 2 - d["r_in"] ** 2))
        elif t == "round_tube":
            a = math.pi / 4 * (d["D"] ** 2 - (d["D"] - 2 * d["t"]) ** 2)
        elif t == "angle":
            a = (d["t"] * (d["d"] + d["b"] - d["t"])
                 + (1 - math.pi / 4) * (d["r1"] ** 2 - 2 * d["r2"] ** 2))
        else:
            continue
        n += 1
        assert _close(a, p["A"], 0.02), (s["name"], a, p["A"])
    assert n > 50


@pytest.mark.parametrize("std", STANDARDS)
def test_angle_principal_axes(std):
    n = 0
    for s in _shapes(std):
        if s["type"] != "angle":
            continue
        n += 1
        p = s["props"]
        assert _close(p["I_major"] + p["I_minor"], p["I33"] + p["I22"], 0.02), s["name"]
        assert p["I_major"] >= max(p["I33"], p["I22"]) * 0.995, s["name"]
        assert p["I_minor"] <= min(p["I33"], p["I22"]) * 1.005, s["name"]
        assert 0.0 < p["alpha"] <= math.pi / 4 + 1e-6, s["name"]
        if s["dims"]["d"] == s["dims"]["b"]:
            assert abs(p["alpha"] - math.pi / 4) < 1e-6, s["name"]
        # shear centre at the leg intersection lies toward the heel
        assert p["x0"] < 0 and p["y0"] < 0, s["name"]
    assert n > 0


@pytest.mark.parametrize("std", STANDARDS)
def test_round_tubes_closed_form(std):
    for s in _shapes(std):
        if s["type"] == "round_tube":
            p = s["props"]
            assert _close(p["J"], 2 * p["I33"], 0.01), s["name"]
            assert p["I33"] == p["I22"] and p["Cw"] == 0.0


@pytest.mark.parametrize("std", STANDARDS)
def test_channels_shear_centre_behind_web(std):
    for s in _shapes(std):
        if s["type"] == "channel":
            p = s["props"]
            assert p["x0"] < 0 and p["y0"] == 0.0, s["name"]
            assert p["I33"] > p["I22"], s["name"]


def test_counts_per_standard():
    """Pins the table sizes documented in SECTION_SOURCES.md."""
    counts = {std: len(_shapes(std)) for std in STANDARDS}
    assert counts == {"eurocode": 1202, "is808": 723, "bs": 1354, "as_nzs": 435}
