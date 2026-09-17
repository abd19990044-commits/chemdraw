from geometry_normalizer import normalize_geometry_xyz
from psi4_extension_v3 import generate_psi4_input_v3


BENZENE_MOL = """benzene
  PubChem

 12 12  0  0  0  0  0  0  0  0999 V2000
   -1.2131   -0.6884    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.2028    0.7064    0.0001 C   0  0  0  0  0  0  0  0  0  0  0  0
   -0.0103   -1.3948    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0104    1.3948   -0.0001 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2028   -0.7063    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2131    0.6884    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -2.1577   -1.2244    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
   -2.1393    1.2564    0.0001 H   0  0  0  0  0  0  0  0  0  0  0  0
   -0.0184   -2.4809   -0.0001 H   0  0  0  0  0  0  0  0  0  0  0  0
    0.0184    2.4808    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
    2.1394   -1.2563    0.0001 H   0  0  0  0  0  0  0  0  0  0  0  0
    2.1577    1.2245    0.0000 H   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  2  0  0  0  0
  1  3  1  0  0  0  0
  1  7  1  0  0  0  0
  2  4  1  0  0  0  0
  2  8  1  0  0  0  0
  3  5  2  0  0  0  0
  3  9  1  0  0  0  0
  4  6  2  0  0  0  0
  4 10  1  0  0  0  0
  5  6  1  0  0  0  0
  5 11  1  0  0  0  0
  6 12  1  0  0  0  0
M  END
"""


def _psi_data(coords):
    return {
        "coords": coords,
        "charge": 0,
        "mult": 1,
        "cores": 4,
        "psi_memory_mb": 4000,
        "psi_family": "dft",
        "psi_method": "bp86",
        "psi_basis": "def2-svp",
        "psi_scf_type": "df",
        "psi_convergence": "standard",
        "psi_solvation": "none",
        "psi_calc_type": "sp",
    }


def test_mol_block_becomes_element_first_xyz_only():
    xyz = normalize_geometry_xyz(BENZENE_MOL)
    lines = xyz.splitlines()
    assert len(lines) == 12
    assert lines[0].startswith("C ")
    assert lines[6].startswith("H ")
    assert not any(line.startswith("12 12") for line in lines)
    assert not any(line.split()[:2] == ["1", "2"] for line in lines)


def test_psi4_generator_never_emits_mol_counts_or_bond_table():
    text = generate_psi4_input_v3(_psi_data(BENZENE_MOL))
    assert "12 12  0  0" not in text
    assert "1  2  2  0" not in text
    assert "C  -1.2131000000 -0.6884000000  0.0000000000" in text
    assert "energy('bp86')" in text


def test_standard_xyz_header_is_removed_cleanly():
    xyz = """3
water
O 0.0 0.0 0.0
H 0.7586 0.0 0.5043
H -0.7586 0.0 0.5043
"""
    clean = normalize_geometry_xyz(xyz)
    assert len(clean.splitlines()) == 3
    assert clean.splitlines()[0].startswith("O ")


def test_bond_table_cannot_be_misread_as_xyz():
    bad = """12 12 0 0
1 2 2 0
1 3 1 0
"""
    try:
        normalize_geometry_xyz(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("Connectivity table must never be accepted as Cartesian XYZ coordinates")
