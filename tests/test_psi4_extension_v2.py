import pytest

from psi4_extension_v2 import generate_psi4_input_v2


COORDS = """O 0.000000 0.000000 0.000000
H 0.758602 0.000000 0.504284
H -0.758602 0.000000 0.504284"""


def data(**overrides):
    d = {
        "coords": COORDS,
        "name": "water",
        "charge": 0,
        "mult": 1,
        "cores": 4,
        "psi_memory_mb": 4000,
        "psi_family": "dft",
        "psi_method": "b3lyp",
        "psi_basis": "def2-tzvp",
        "psi_scf_type": "df",
        "psi_convergence": "tight",
        "psi_solvation": "none",
        "psi_calc_type": "sp",
        "psi_scf_guess": "sad",
        "psi_scf_maxiter": 100,
        "psi_dft_grid": "default",
        "psi_geom_convergence": "QCHEM",
        "psi_geom_maxiter": 50,
        "psi_output": "none",
    }
    d.update(overrides)
    return d


def test_v2_scf_guess_and_maxiter():
    text = generate_psi4_input_v2(data())
    assert "guess sad" in text
    assert "maxiter 100" in text


def test_v2_gradient_driver():
    text = generate_psi4_input_v2(data(psi_calc_type="gradient"))
    assert "gradient('b3lyp')" in text


def test_v2_hessian_driver():
    text = generate_psi4_input_v2(data(psi_calc_type="hessian"))
    assert "hessian('b3lyp')" in text


def test_v2_fine_dft_grid():
    text = generate_psi4_input_v2(data(psi_dft_grid="fine"))
    assert "dft_radial_points 99" in text
    assert "dft_spherical_points 590" in text


def test_v2_veryfine_dft_grid():
    text = generate_psi4_input_v2(data(psi_dft_grid="veryfine"))
    assert "dft_radial_points 150" in text
    assert "dft_spherical_points 974" in text


def test_v2_geometry_controls():
    text = generate_psi4_input_v2(data(
        psi_calc_type="opt",
        psi_geom_convergence="GAU_TIGHT",
        psi_geom_maxiter=100,
    ))
    assert "g_convergence GAU_TIGHT" in text
    assert "geom_maxiter 100" in text
    assert "optimize('b3lyp')" in text


def test_v2_molden_and_fchk_outputs_use_return_wfn():
    text = generate_psi4_input_v2(data(psi_output="both", psi_print_variables=True))
    assert "return_wfn=True" in text
    assert "molden(wfn, 'water.molden')" in text
    assert "fchk(wfn, 'water.fchk')" in text
    assert "print_variables()" in text


def test_v2_dispersion_corrected_dft_alias_is_preserved():
    text = generate_psi4_input_v2(data(psi_method="b3lyp-d3bj2b"))
    assert "energy('b3lyp-d3bj2b')" in text


def test_v2_mp3_and_omp2_are_valid_driver_targets():
    assert "energy('mp3')" in generate_psi4_input_v2(data(psi_family="mp2", psi_method="mp3"))
    assert "energy('omp2')" in generate_psi4_input_v2(data(psi_family="mp2", psi_method="omp2"))


def test_v2_rejects_ccsdt_geometry_optimization():
    with pytest.raises(ValueError, match="CCSD\\(T\\)"):
        generate_psi4_input_v2(data(
            psi_family="cc",
            psi_method="ccsd(t)",
            psi_calc_type="opt",
        ))
