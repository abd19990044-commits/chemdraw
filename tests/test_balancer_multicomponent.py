from xml.etree import ElementTree as ET

from cdxml_export import generate_reaction_cdxml
from reaction_balancer import balance_reaction_smiles


def test_multicomponent_combustion_balances_with_smallest_positive_solution():
    # phenol + benzene + oxygen -> carbon dioxide + water
    result = balance_reaction_smiles(
        ["Oc1ccccc1", "c1ccccc1", "O=O"],
        ["O=C=O", "O"],
    )
    assert result == ([1, 2, 22], [18, 9])


def test_cdxml_kekulizes_benzene_ring_for_chemdraw():
    data = generate_reaction_cdxml(
        [(1, "c1ccccc1")],
        [(1, "c1ccccc1")],
        small_as_formula=False,
    )
    assert data is not None
    root = ET.fromstring(data)
    fragments = root.findall("page/fragment")
    assert len(fragments) == 2
    for fragment in fragments:
        orders = [bond.get("Order") for bond in fragment.findall("b")]
        assert orders.count("2") == 3
        assert orders.count("1") == 3
        assert "1.5" not in orders
