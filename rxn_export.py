from __future__ import annotations

"""ChemDraw-oriented MDL RXN export helpers.

MDL RXN is a chemical exchange format: it stores reaction components but not a
full ChemDraw page layout (arrow length, conditions typography, coefficient
labels, etc.).  This exporter therefore focuses on the part RXN *can* preserve
reliably: molecular graph, stereochemistry, formal charges and stable 2D
orientation matching the publication renderer as closely as possible.
"""

import io
from fractions import Fraction
from math import gcd
from functools import reduce

from rdkit import Chem
from rdkit.Chem import AllChem, rdChemReactions


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // gcd(a, b) if a and b else 0


def _canonical_2d_mol(smiles: str) -> Chem.Mol | None:
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return None
    mol = Chem.Mol(mol)
    try:
        AllChem.Compute2DCoords(mol, canonOrient=True)
        Chem.rdDepictor.StraightenDepiction(mol)
    except Exception:
        try:
            AllChem.Compute2DCoords(mol, canonOrient=True)
        except Exception:
            return None
    return mol


def _integer_scale(reactant_pairs, product_pairs):
    coeffs = [Fraction(c) for c, _ in list(reactant_pairs) + list(product_pairs)]
    den = reduce(_lcm, (c.denominator for c in coeffs), 1)
    scaled_r = [(Fraction(c) * den, s) for c, s in reactant_pairs]
    scaled_p = [(Fraction(c) * den, s) for c, s in product_pairs]
    nums = [int(c) for c, _ in scaled_r + scaled_p]
    common = reduce(gcd, (abs(n) for n in nums if n), 0) or 1
    scaled_r = [(int(c) // common, s) for c, s in scaled_r]
    scaled_p = [(int(c) // common, s) for c, s in scaled_p]
    if any(c <= 0 for c, _ in scaled_r + scaled_p):
        return None
    return scaled_r, scaled_p


def generate_chemdraw_rxn_file(
    reactant_pairs,
    product_pairs,
    filename: str = "Reaction.rxn",
    title: str = "",
) -> io.BytesIO | None:
    """Generate an MDL RXN with stable canonical 2D component coordinates.

    Stoichiometric coefficients in classic RXN are represented by repeated
    reaction components.  That is intrinsic to the format and ChemDraw may
    display those repeats explicitly.  We deliberately do *not* call
    ``Compute2DCoordsForReaction`` because it may rotate/reorient components
    independently of the publication renderer.
    """
    scaled = _integer_scale(reactant_pairs, product_pairs)
    if scaled is None:
        return None
    reactants, products = scaled

    rxn = rdChemReactions.ChemicalReaction()
    try:
        for coeff, smiles in reactants:
            mol = _canonical_2d_mol(smiles)
            if mol is None:
                return None
            for _ in range(coeff):
                rxn.AddReactantTemplate(Chem.Mol(mol))
        for coeff, smiles in products:
            mol = _canonical_2d_mol(smiles)
            if mol is None:
                return None
            for _ in range(coeff):
                rxn.AddProductTemplate(Chem.Mol(mol))

        block = rdChemReactions.ReactionToRxnBlock(rxn)
        if title:
            lines = block.splitlines()
            if len(lines) > 1:
                lines[1] = title[:80]
                block = "\n".join(lines) + ("\n" if not block.endswith("\n") else "")

        # Round-trip validation catches malformed RXN before it reaches users.
        check = rdChemReactions.ReactionFromRxnBlock(block, sanitize=True, removeHs=False)
        if check is None:
            return None
    except Exception:
        return None

    out = io.BytesIO(block.encode("utf-8"))
    out.name = filename
    out.seek(0)
    return out


def rxn_visual_limit_note() -> str:
    return (
        "MDL RXN preserves chemical connectivity/stereochemistry and component "
        "coordinates, but ChemDraw may re-layout the page and show stoichiometric "
        "coefficients as repeated components because classic RXN does not store "
        "the publication arrow/condition/label layout. Use the SVG for exact "
        "visual fidelity to the Telegram preview."
    )
