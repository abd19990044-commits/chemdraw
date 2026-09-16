from __future__ import annotations

from fractions import Fraction
from xml.etree import ElementTree as ET

from rdkit import Chem
from rdkit.Chem import rdDepictor

import publication_renderer_v2 as pub2


BOND_LENGTH = 14.4
MOL_GAP = 22.0
PLUS_GAP = 14.0
ARROW_WIDTH = 72.0
PAGE_MARGIN = 24.0
BASELINE_Y = 110.0
LABEL_SIZE = 10.0
CAPTION_SIZE = 10.0
TEXT_SIZE = 10.0

_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _coef_text(value) -> str:
    f = Fraction(value)
    if f == 1:
        return ""
    if f.denominator == 1:
        return str(f.numerator)
    return f"{f.numerator}/{f.denominator}"


def _display_text(text: str) -> str:
    return "".join(ch.translate(_SUBSCRIPT) if ch.isdigit() else ch for ch in text)


def _prepare_mol(smiles: str) -> Chem.Mol | None:
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return None
    mol = Chem.Mol(mol)
    Chem.AssignStereochemistry(mol, cleanIt=True, force=True)
    try:
        Chem.Kekulize(mol, clearAromaticFlags=True)
    except Exception:
        pass
    rdDepictor.Compute2DCoords(mol, canonOrient=True, clearConfs=True)
    try:
        rdDepictor.StraightenDepiction(mol, minimizeRotation=True)
    except Exception:
        pass
    try:
        Chem.WedgeMolBonds(mol, mol.GetConformer())
    except Exception:
        pass
    return mol


def _mol_extent(mol: Chem.Mol) -> tuple[float, float, float, float]:
    conf = mol.GetConformer()
    xs = [conf.GetAtomPosition(i).x for i in range(mol.GetNumAtoms())]
    ys = [conf.GetAtomPosition(i).y for i in range(mol.GetNumAtoms())]
    return min(xs), max(xs), min(ys), max(ys)


def _bond_order_value(bond: Chem.Bond) -> str:
    bt = bond.GetBondType()
    if bt == Chem.BondType.SINGLE:
        return "1"
    if bt == Chem.BondType.DOUBLE:
        return "2"
    if bt == Chem.BondType.TRIPLE:
        return "3"
    if bt == Chem.BondType.AROMATIC:
        return "1.5"
    return "1"


def _add_text(page: ET.Element, obj_id: int, x: float, y: float, text: str, size: float = TEXT_SIZE, bold: bool = False) -> int:
    t = ET.SubElement(page, "t", {"id": str(obj_id), "p": f"{x:.2f} {y:.2f}", "Justification": "Center"})
    s_attrs = {"font": "3", "size": f"{float(size):g}"}
    if bold:
        s_attrs["face"] = "1"
    s = ET.SubElement(t, "s", s_attrs)
    s.text = text
    return obj_id + 1


def _add_plain_line(page: ET.Element, obj_id: int, x1: float, y1: float, x2: float, y2: float) -> int:
    ET.SubElement(page, "graphic", {"id": str(obj_id), "GraphicType": "Line", "BoundingBox": f"{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f}"})
    return obj_id + 1


def _add_fragment(page: ET.Element, obj_id: int, mol: Chem.Mol, x0: float, center_y: float) -> tuple[int, float]:
    minx, maxx, miny, maxy = _mol_extent(mol)
    width = max((maxx - minx) * BOND_LENGTH, BOND_LENGTH)
    frag = ET.SubElement(page, "fragment", {"id": str(obj_id)})
    obj_id += 1
    conf = mol.GetConformer()
    atom_ids: dict[int, int] = {}
    for i, atom in enumerate(mol.GetAtoms()):
        p = conf.GetAtomPosition(i)
        x = x0 + (p.x - minx) * BOND_LENGTH
        y = center_y - (p.y - (miny + maxy) / 2.0) * BOND_LENGTH
        attrs = {"id": str(obj_id), "p": f"{x:.2f} {y:.2f}", "Element": str(atom.GetAtomicNum())}
        charge = atom.GetFormalCharge()
        if charge:
            attrs["Charge"] = str(charge)
        isotope = atom.GetIsotope()
        if isotope:
            attrs["Isotope"] = str(isotope)
        amap = atom.GetAtomMapNum()
        if amap:
            attrs["AtomMap"] = str(amap)
        ET.SubElement(frag, "n", attrs)
        atom_ids[i] = obj_id
        obj_id += 1
    for bond in mol.GetBonds():
        attrs = {"id": str(obj_id), "B": str(atom_ids[bond.GetBeginAtomIdx()]), "E": str(atom_ids[bond.GetEndAtomIdx()]), "Order": _bond_order_value(bond)}
        direction = bond.GetBondDir()
        if direction == Chem.BondDir.BEGINWEDGE:
            attrs["Display"] = "WedgeBegin"
        elif direction == Chem.BondDir.BEGINDASH:
            attrs["Display"] = "WedgedHashBegin"
        elif direction == Chem.BondDir.ENDUPRIGHT:
            attrs["Display"] = "WedgeEnd"
        elif direction == Chem.BondDir.ENDDOWNRIGHT:
            attrs["Display"] = "WedgedHashEnd"
        ET.SubElement(frag, "b", attrs)
        obj_id += 1
    return obj_id, width


def _new_cdxml_root(width: float, height: float) -> tuple[ET.Element, ET.Element]:
    root = ET.Element("CDXML", {
        "BoundingBox": f"0 0 {width:.0f} {height:.0f}",
        "BondLength": f"{BOND_LENGTH:g}",
        "LabelFont": "3", "LabelSize": f"{LABEL_SIZE:g}",
        "CaptionFont": "3", "CaptionSize": f"{CAPTION_SIZE:g}",
        "LineWidth": "0.6", "BoldWidth": "2", "HashSpacing": "2.5", "MarginWidth": "1.6",
    })
    colors = ET.SubElement(root, "colortable")
    ET.SubElement(colors, "color", {"r": "1", "g": "1", "b": "1"})
    ET.SubElement(colors, "color", {"r": "0", "g": "0", "b": "0"})
    fonts = ET.SubElement(root, "fonttable")
    ET.SubElement(fonts, "font", {"id": "3", "charset": "unicode", "name": "Arial"})
    page = ET.SubElement(root, "page", {"id": "1", "BoundingBox": f"0 0 {width:.0f} {height:.0f}", "Width": f"{width:.0f}", "Height": f"{height:.0f}"})
    return root, page


def generate_single_compound_cdxml(smiles: str, title: str = "Compound") -> bytes | None:
    mol = _prepare_mol(smiles)
    if mol is None:
        return None
    minx, maxx, miny, maxy = _mol_extent(mol)
    mol_w = max((maxx - minx) * BOND_LENGTH, BOND_LENGTH)
    mol_h = max((maxy - miny) * BOND_LENGTH, BOND_LENGTH)
    width = max(240.0, mol_w + 2 * PAGE_MARGIN)
    height = max(180.0, mol_h + 2 * PAGE_MARGIN)
    root, page = _new_cdxml_root(width, height)
    x0 = (width - mol_w) / 2.0
    _add_fragment(page, 2, mol, x0, height / 2.0)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _text_width(text: str) -> float:
    return max(10.0, 5.8 * len(text))


def _add_formula_or_condensed(page: ET.Element, obj_id: int, x0: float, center_y: float, label: str) -> tuple[int, float]:
    parts = pub2._split_condensed(label)
    if not any(kind == "bond" for kind, _ in parts):
        shown = _display_text(label)
        width = _text_width(shown)
        obj_id = _add_text(page, obj_id, x0 + width / 2, center_y + 3, shown, size=10)
        return obj_id, width
    x = x0
    for kind, token in parts:
        if kind == "group":
            shown = _display_text(token)
            width = _text_width(shown)
            obj_id = _add_text(page, obj_id, x + width / 2, center_y + 3, shown, size=10)
            x += width
            continue
        bond_width = 16.0
        x1, x2 = x + 2.0, x + bond_width - 2.0
        offsets = {"-": [0.0], "=": [-1.8, 1.8], "#": [-2.5, 0.0, 2.5]}[token]
        for dy in offsets:
            obj_id = _add_plain_line(page, obj_id, x1, center_y + dy, x2, center_y + dy)
        x += bond_width
    return obj_id, x - x0


def _display_label(smiles: str, small_as_formula: bool) -> str | None:
    if not small_as_formula:
        return None
    condensed = pub2._condensed_for_display(smiles)
    if condensed:
        return condensed
    return pub2.base.formula_for_display(smiles)


def generate_reaction_cdxml(reactant_pairs, product_pairs, *, arrow_top: str = "", arrow_bottom: str = "", arrow_style: str = "forward", title: str = "Reaction", small_as_formula: bool = True) -> bytes | None:
    def prepare_side(pairs):
        out = []
        for coef, smiles in pairs:
            label = _display_label(smiles, small_as_formula)
            if label:
                out.append((Fraction(coef), "label", label))
            else:
                mol = _prepare_mol(smiles)
                if mol is None:
                    return None
                out.append((Fraction(coef), "mol", mol))
        return out
    reactants = prepare_side(reactant_pairs)
    products = prepare_side(product_pairs)
    if not reactants or not products:
        return None
    root, page = _new_cdxml_root(800, 260)
    obj_id = 2
    def add_side(items, x_start: float) -> float:
        nonlocal obj_id
        xcur = x_start
        for idx, (coef, kind, payload) in enumerate(items):
            ctext = _coef_text(coef)
            if ctext:
                obj_id = _add_text(page, obj_id, xcur + 4, BASELINE_Y + 3, ctext, size=10)
                xcur += 14
            if kind == "label":
                obj_id, width = _add_formula_or_condensed(page, obj_id, xcur, BASELINE_Y, payload)
            else:
                obj_id, width = _add_fragment(page, obj_id, payload, xcur, BASELINE_Y)
            xcur += width
            if idx != len(items) - 1:
                xcur += PLUS_GAP
                obj_id = _add_text(page, obj_id, xcur, BASELINE_Y + 3, "+", size=11)
                xcur += PLUS_GAP
        return xcur
    x = add_side(reactants, PAGE_MARGIN) + MOL_GAP
    arrow_x1, arrow_x2, arrow_y = x, x + ARROW_WIDTH, BASELINE_Y
    graphic_attrs = {"id": str(obj_id), "GraphicType": "Line", "BoundingBox": f"{arrow_x2:.2f} {arrow_y:.2f} {arrow_x1:.2f} {arrow_y:.2f}", "ArrowType": "FullHead", "HeadSize": "750"}
    if arrow_style == "equilibrium":
        graphic_attrs["ArrowType"] = "Equilibrium"
    elif arrow_style == "reversible":
        graphic_attrs["ArrowheadHead"] = "Full"
        graphic_attrs["ArrowheadTail"] = "Full"
    ET.SubElement(page, "graphic", graphic_attrs)
    obj_id += 1
    if arrow_top:
        obj_id = _add_text(page, obj_id, (arrow_x1 + arrow_x2) / 2, arrow_y - 18, arrow_top, size=10)
    if arrow_bottom:
        obj_id = _add_text(page, obj_id, (arrow_x1 + arrow_x2) / 2, arrow_y + 20, arrow_bottom, size=10)
    x = add_side(products, arrow_x2 + MOL_GAP)
    page_width = max(420, x + PAGE_MARGIN)
    page.set("BoundingBox", f"0 0 {page_width:.0f} 220")
    page.set("Width", f"{page_width:.0f}")
    page.set("Height", "220")
    root.set("BoundingBox", page.get("BoundingBox", "0 0 800 260"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
