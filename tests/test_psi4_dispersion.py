from psi4_extension_v4 import apply_dispersion_alias, dispersion_options_for_method, generate_psi4_input_v4


COORDS = """C 0.000000 0.000000 0.000000
H 0.000000 0.000000 1.089000
H 1.026719 0.000000 -0.363000
H -0.513360 -0.889165 -0.363000
H -0.513360 0.889165 -0.363000"""


def base_data(**overrides):
    data = {
        "coords": COORDS,
        "charge": 0,
        "mult": 1,
        "cores": 4,
        "psi_memory_mb": 4000,
        "psi_family": "dft",
        "psi_method": "b3lyp",
        "psi_base_method": "b3lyp",
        "psi_dispersion": "none",
        "psi_basis": "def2-svp",
        "psi_scf_type": "df",
        "psi_convergence": "standard",
        "psi_solvation": "none",
        "psi_calc_type": "sp",
    }
    data.update(overrides)
    return data


def test_b3lyp_d3bj_alias():
    assert apply_dispersion_alias("b3lyp", "d3bj") == "b3lyp-d3bj2b"
    text = generate_psi4_input_v4(base_data(psi_dispersion="d3bj"))
    assert "energy('b3lyp-d3bj2b')" in text


def test_pbe0_d3zero_alias():
    assert apply_dispersion_alias("pbe0", "d3zero") == "pbe0-d3zero2b"


def test_b3lyp_d4_alias():
    text = generate_psi4_input_v4(base_data(psi_dispersion="d4"))
    assert "energy('b3lyp-d4')" in text


def test_builtin_nonlocal_functional_does_not_offer_extra_dispersion():
    opts = dispersion_options_for_method("wb97m-v")
    assert opts == {"Built in / already included": "none"}


def test_unsupported_combination_is_rejected():
    try:
        apply_dispersion_alias("m06-2x", "d3bj")
    except ValueError as exc:
        assert "not enabled" in str(exc)
    else:
        raise AssertionError("Unsupported dispersion combination must be rejected")


def test_precorrected_method_rejects_double_counting():
    try:
        apply_dispersion_alias("b3lyp-d3bj2b", "d4")
    except ValueError as exc:
        assert "double counting" in str(exc)
    else:
        raise AssertionError("Double dispersion correction must be rejected")
