from advanced_chemistry import (
    balance_redox,
    conservative_atom_map,
    format_net_ionic,
    net_ionic_equation,
    parse_mechanism_arrows,
    render_mechanism_svg,
)


def test_redox_fe2_fe3_adds_electron():
    result = balance_redox(["[Fe+2]"], ["[Fe+3]"], "acidic")
    assert result is not None
    labels_left = [x.label for x in result.reactants]
    labels_right = [x.label for x in result.products]
    assert "e-" in labels_left or "e-" in labels_right
    assert labels_left.count("e-") + labels_right.count("e-") == 1


def test_net_ionic_strong_acid_base():
    result = net_ionic_equation(
        [(1, "Cl"), (1, "[Na+].[OH-]")],
        [(1, "[Na+].[Cl-]"), (1, "O")],
    )
    assert result is not None
    text = format_net_ionic(result)
    assert "H+" in text
    assert "OH-" in text
    assert "H2O" in text
    assert "Na+" in result.cancelled
    assert "Cl-" in result.cancelled


def test_conservative_atom_mapping_unique_identity():
    mapped = conservative_atom_map(["CCO"], ["CCO"])
    assert mapped is not None
    assert ">>" in mapped
    assert ":1" in mapped


def test_mechanism_parser_and_svg():
    arrows = parse_mechanism_arrows("1->2,3~>2")
    assert arrows == [(1, 2, 2), (3, 2, 1)]
    svg = render_mechanism_svg("CCO", arrows)
    assert svg is not None
    text = svg.decode("utf-8")
    assert "pairArrow" in text
    assert "fishArrow" in text
    assert "<svg" in text


def test_mechanism_index_preview_without_arrows():
    svg = render_mechanism_svg("CCO", [])
    assert svg is not None
    assert b"<svg" in svg
