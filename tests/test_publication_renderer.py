from fractions import Fraction

from rdkit import Chem

import publication_renderer as pub


def _smiles(text: str) -> str:
    mol = Chem.MolFromSmiles(text)
    assert mol is not None
    return Chem.MolToSmiles(mol)


def test_parser_preserves_unbracketed_positive_charge():
    terms = pub.split_reaction_side("NH4+ + OH-")
    assert [t.name for t in terms] == ["NH4+", "OH-"]


def test_parser_preserves_bracketed_smiles_charge():
    terms = pub.split_reaction_side("[NH4+] + [OH-]")
    assert [t.name for t in terms] == ["[NH4+]", "[OH-]"]


def test_parser_supports_fractional_and_decimal_coefficients():
    terms = pub.split_reaction_side("1/2 O2 + 0.5 H2 + 2 H2O")
    assert [t.coefficient for t in terms] == [Fraction(1, 2), Fraction(1, 2), Fraction(2, 1)]


def test_parser_does_not_treat_terminal_plus_as_separator():
    terms = pub.split_reaction_side("Fe3+")
    assert len(terms) == 1
    assert terms[0].name == "Fe3+"


def test_formula_display_for_small_species():
    assert pub.formula_for_display("O=O") == "O2"
    assert pub.formula_for_display("[NH4+]") == "NH4+"
    assert pub.formula_for_display("O") == "H2O"


def test_png_renderer_produces_nontrivial_png():
    reactants = [(2, _smiles("[H][H]")), (1, _smiles("O=O"))]
    products = [(2, _smiles("O"))]
    data = pub.render_reaction_png(reactants, products, arrow_top="Pt", arrow_bottom="25 ^oC")
    assert data is not None
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) > 1000


def test_svg_renderer_is_true_vector_svg_with_annotations():
    reactants = [(1, _smiles("CC(=O)O")), (1, _smiles("N"))]
    products = [(1, _smiles("CC(=O)N")), (1, _smiles("O"))]
    data = pub.render_reaction_svg(reactants, products, arrow_top="cat.", arrow_bottom="80 ^oC")
    assert data is not None
    text = data.decode("utf-8")
    assert "<svg" in text
    assert "<line" in text
    assert "cat." in text
    assert "80" in text


def test_large_organic_species_are_drawn_as_structures_not_formulae():
    aspirin = "CC(=O)Oc1ccccc1C(=O)O"
    assert pub.formula_for_display(aspirin) is None
