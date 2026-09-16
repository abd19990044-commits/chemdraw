from fractions import Fraction

from rdkit.Chem import rdChemReactions

from rxn_export import generate_chemdraw_rxn_file


def test_rxn_roundtrip_and_coordinates():
    rxn_file = generate_chemdraw_rxn_file(
        [(1, "C=C"), (1, "[H][H]")],
        [(1, "CC")],
        title="Ethene hydrogenation",
    )
    assert rxn_file is not None
    block = rxn_file.getvalue().decode("utf-8")
    assert block.startswith("$RXN")
    rxn = rdChemReactions.ReactionFromRxnBlock(block, sanitize=True, removeHs=False)
    assert rxn is not None
    assert rxn.GetNumReactantTemplates() == 2
    assert rxn.GetNumProductTemplates() == 1

    for mol in list(rxn.GetReactants()) + list(rxn.GetProducts()):
        assert mol.GetNumConformers() == 1
        conf = mol.GetConformer()
        assert not conf.Is3D()
        pts = [conf.GetAtomPosition(i) for i in range(mol.GetNumAtoms())]
        # At least one non-zero coordinate for multi-atom structures.
        if mol.GetNumAtoms() > 1:
            assert any(abs(p.x) > 1e-6 or abs(p.y) > 1e-6 for p in pts)


def test_rxn_fractional_coefficients_scale_to_smallest_integers():
    rxn_file = generate_chemdraw_rxn_file(
        [(Fraction(1, 2), "O=O"), (1, "[H][H]")],
        [(1, "O")],
    )
    assert rxn_file is not None
    rxn = rdChemReactions.ReactionFromRxnBlock(
        rxn_file.getvalue().decode("utf-8"), sanitize=True, removeHs=False
    )
    assert rxn is not None
    # 1/2 O2 + H2 -> H2O scales to O2 + 2 H2 -> 2 H2O in classic RXN.
    assert rxn.GetNumReactantTemplates() == 3
    assert rxn.GetNumProductTemplates() == 2
