from __future__ import annotations

"""ChemDraw-oriented CDXML reaction export.

The exporter writes editable atom/bond fragments, stoichiometric coefficient
labels, reaction arrow and condition labels into one CDXML page.  It is meant
as the primary ChemDraw editing format; MDL RXN remains a compatibility export.
"""

from fractions import Fraction
from html import escape
from xml.etree import ElementTree as ET

from rdkit import Chem
from rdkit.Chem import rdDepictor


BOND_LENGTH = 22.0
MOL_GAP = 34.0
PLUS_GAP = 24.0
ARROW_WIDTH = 110.0
PAGE_MARGIN = 36.0
BASELINE_Y = 180.0


def _coef_text(value) -> str:
    f = Fraction(value)
    if f == 1:
        return ""
    if f.denominator == 1:
        return str(f.numerator)
    return f"{f.numerator}/{f.denominator}"


def _prepare_mol(smiles: str) -> Chem.Mol | None:
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return None
    mol = Chem.Mol(mol)
    rdDepictor.Compute2DCoords(mol, canonOrient=True, clearConfs=True)
    try:
        rdDepictor.StraightenDepiction(mol, minimizeRotation=True)
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


def _add_text(page: ET.Element, obj_id: int, x: float, y: float, text: str, size: int = 11, bold: bool = False) -> int:
    t = ET.SubElement(page, "t", {"id": str(obj_id), "p": f"{x:.2f} {y:.2f}", "Justification": "Center"})
    s_attrs = {"font": "3", "size": str(size * 20)}
    if bold:
        s_attrs["face"] = "1"
    s = ET.SubElement(t, "s", s_attrs)
    s.text = text
    return obj_id + 1


def _add_fragment(page: ET.Element, obj_id: int, mol: Chem.Mol, x0: float, center_y: float) -> tuple[int, float]:
    minx, maxx, miny, maxy = _mol_extent(mol)
    width = max((maxx - minx) * BOND_LENGTH, BOND_LENGTH)
    height = max((maxy - miny) * BOND_LENGTH, BOND_LENGTH)
    frag = ET.SubElement(page, "fragment", {"id": str(obj_id)})
    obj_id += 1
    conf = mol.GetConformer()
    atom_ids: dict[int, int] = {}
    for i, atom in enumerate(mol.GetAtoms()):
        p = conf.GetAtomPosition(i)
        x = x0 + (p.x - minx) * BOND_LENGTH
        y = center_y - (p.y - (miny + maxy) / 2.0) * BOND_LENGTH
        attrs = {
            "id": str(obj_id),
            "p": f"{x:.2f} {y:.2f}",
            "Element": str(atom.GetAtomicNum()),
        }
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
        attrs = {
            "id": str(obj_id),
            "B": str(atom_ids[bond.GetBeginAtomIdx()]),
            "E": str(atom_ids[bond.GetEndAtomIdx()]),
            "Order": _bond_order_value(bond),
        }
        direction = bond.GetBondDir()
        if direction == Chem.BondDir.BEGINWEDGE:
            attrs["Display"] = "WedgeBegin"
        elif direction == Chem.BondDir.BEGINDASH:
            attrs["Display"] = "WedgedHashBegin"
        ET.SubElement(frag, "b", attrs)
        obj_id += 1

    return obj_id, width


def generate_reaction_cdxml(
    reactant_pairs,
    product_pairs,
    *,
    arrow_top: str = "",
    arrow_bottom: str = "",
    arrow_style: str = "forward",
    title: str = "Reaction",
) -> bytes | None:
    """Create an editable ChemDraw CDXML reaction document."""
    reactants = []
    products = []
    for coef, smiles in reactant_pairs:
        mol = _prepare_mol(smiles)
        if mol is None:
            return None
        reactants.append((Fraction(coef), mol))
    for coef, smiles in product_pairs:
        mol = _prepare_mol(smiles)
        if mol is None:
            return None
        products.append((Fraction(coef), mol))
    if not reactants or not products:
        return None

    root = ET.Element("CDXML", {
        "CreationProgram": "Chemistry Drawing Bot",
        "Name": title[:80],
        "BoundingBox": "0 0 1200 420",
        "BondLength": str(BOND_LENGTH),
        "LabelFont": "3",
        "LabelSize": "200",
        "CaptionFont": "3",
        "CaptionSize": "180",
    })
    color_table = ET.SubElement(root, "colortable")
    ET.SubElement(color_table, "color", {"r": "1", "g": "1", "b": "1"})
    ET.SubElement(color_table, "color", {"r": "0", "g": "0", "b": "0"})
    font_table = ET.SubElement(root, "fonttable")
    ET.SubElement(font_table, "font", {"id": "3", "charset": "iso-8859-1", "name": "Arial"})
    page = ET.SubElement(root, "page", {"id": "1", "BoundingBox": "0 0 1200 420", "Width": "1200", "Height": "420"})

    obj_id = 2
    x = PAGE_MARGIN

    def add_side(items, x_start: float) -> tuple[int, float]:
        nonlocal obj_id
        xcur = x_start
        for idx, (coef, mol) in enumerate(items):
            ctext = _coef_text(coef)
            if ctext:
                obj_id = _add_text(page, obj_id, xcur + 6, BASELINE_Y + 4, ctext, size=12)
                xcur += 22
            obj_id, w = _add_fragment(page, obj_id, mol, xcur, BASELINE_Y)
            xcur += w
            if idx != len(items) - 1:
                xcur += PLUS_GAP
                obj_id = _add_text(page, obj_id, xcur, BASELINE_Y + 4, "+", size=13)
                xcur += PLUS_GAP
        return obj_id, xcur

    _, x = add_side(reactants, x)
    x += MOL_GAP
    arrow_x1 = x
    arrow_x2 = x + ARROW_WIDTH
    arrow_y = BASELINE_Y

    graphic_attrs = {
        "id": str(obj_id),
        "GraphicType": "Line",
        "BoundingBox": f"{arrow_x1:.2f} {arrow_y:.2f} {arrow_x2:.2f} {arrow_y:.2f}",
        "ArrowType": "FullHead",
        "HeadSize": "1000",
    }
    if arrow_style == "equilibrium":
        graphic_attrs["ArrowType"] = "Equilibrium"
    elif arrow_style == "reversible":
        graphic_attrs["ArrowType"] = "FullHead"
        graphic_attrs["ArrowheadHead"] = "Full"
        graphic_attrs["ArrowheadTail"] = "Full"
    ET.SubElement(page, "graphic", graphic_attrs)
    obj_id += 1

    if arrow_top:
        obj_id = _add_text(page, obj_id, (arrow_x1 + arrow_x2) / 2, arrow_y - 28, arrow_top, size=10)
    if arrow_bottom:
        obj_id = _add_text(page, obj_id, (arrow_x1 + arrow_x2) / 2, arrow_y + 30, arrow_bottom, size=10)

    x = arrow_x2 + MOL_GAP
    _, x = add_side(products, x)

    page.set("BoundingBox", f"0 0 {max(600, x + PAGE_MARGIN):.0f} 360")
    page.set("Width", f"{max(600, x + PAGE_MARGIN):.0f}")
    root.set("BoundingBox", page.get("BoundingBox", "0 0 1200 420"))

    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return xml
