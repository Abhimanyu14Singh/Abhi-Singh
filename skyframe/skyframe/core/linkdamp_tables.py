"""Interactive Database Editing tables for the wave-B link / damping data,
appended to the :mod:`skyframe.core.modeltables` catalogue by one hook
line.  (The new link TYPES need no table of their own: ``links.link_type``
enumerates ``LINK_TYPES`` and "Link Properties - Parameters" edits every
scalar param.)

* ``material_damping`` — Material Properties - Modal Damping Ratios:
  (material, zeta) — composite modal damping (core.composite_damping);
* ``link_freq_props`` — Link Frequency-Dependent Properties (names);
* ``link_freq_points`` — ... - Tables: (property, DOF, f, K, C) rows in SI
  (K kN/m or kN*m/rad, C kN*s/m or kN*m*s/rad, f Hz);
* ``link_freq_links`` — ... - Link Assignments: (property, link).
"""

from __future__ import annotations


def tables(G_DEF: str, G_OBJ: str) -> list:
    from skyframe.core import modeltables as mt
    from skyframe.core import modeltables_ext as mx
    from skyframe.core.link_freq import LFD_DOFS
    Col, num = mt.Col, "number"

    class _Points(mx.ListChildTable):
        def sublists(self, p):
            return [(k, v) for k, v in (p.get("dofs") or {}).items()]

        def store(self, p, by_tag):
            p["dofs"] = {k: v for k, v in by_tag.items() if v}

    return [
        mt.ChildDictTable(
            "material_damping", "Material Properties - Modal Damping Ratios",
            G_DEF, [
                Col("material", "Material", "enum",
                    enum=mt._names("materials"), required=True),
                Col("zeta", "Damping Ratio", num, required=True),
            ], container=None, field="material_damping",
            child_col="material", value_col="zeta"),
        mt.ObjectTable(
            "link_freq_props", "Link Frequency-Dependent Properties", G_DEF,
            [Col("name", "Property", "text", required=True)],
            container="link_freq_props", keycol="name", name_in_obj=False,
            new_obj=lambda d: {"links": [], "dofs": {}}),
        _Points(
            "link_freq_points", "Link Frequency-Dependent Properties - "
            "Tables", G_DEF, [
                Col("property", "Property", "enum",
                    enum=mt._names("link_freq_props"), required=True),
                Col("dof", "DOF", "enum", enum=LFD_DOFS, required=True),
                Col("f", "Frequency (Hz)", num, path=(0,), required=True),
                Col("K", "Stiffness K", num, path=(1,), required=True),
                Col("C", "Damping C", num, path=(2,), default=0.0),
            ], container="link_freq_props", parent_col="property",
            tag_col="dof", entry="pair",
            new_entry=lambda d: [0.0, 0.0, 0.0]),
        mx.ListChildTable(
            "link_freq_links", "Link Frequency-Dependent Properties - Link "
            "Assignments", G_OBJ, [
                Col("property", "Property", "enum",
                    enum=mt._names("link_freq_props"), required=True),
                Col("link", "Link", "ref", ref="links", required=True,
                    path=("v",)),
            ], container="link_freq_props", parent_col="property",
            field="links", entry="scalar", new_entry=lambda d: ""),
    ]
