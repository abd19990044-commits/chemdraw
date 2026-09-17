from psi4_extension import generate_psi4_input


COORDS = """O 0.000000 0.000000 0.000000
H 0.758602 0.000000 0.504284
H -0.758602 0.000000 0.504284"""


def base_data(**overrides):
    data = {
        "coords": COORDS,
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
    }
    data.update(overrides)
    return data


def test_psi4_single_point_psithon():
    text = generate_psi4_input(base_data())
    assert "memory 4000 MB" in text
    assert "set_num_threads(4)" in text
    assert "molecule mol {" in text
    assert "0 1" in text
    assert "basis def2-tzvp" in text
    assert "reference rks" in text
    assert "scf_type df" in text
    assert "energy('b3lyp')" in text


def test_psi4_open_shell_dft_uses_uks():
    text = generate_psi4_input(base_data(mult=2))
    assert "reference uks" in text


def test_psi4_opt_freq_has_two_driver_calls():
    text = generate_psi4_input(base_data(psi_calc_type="optfreq"))
    assert "optimize('b3lyp')" in text
    assert "frequency('b3lyp')" in text


def test_psi4_transition_state_sets_optking_controls_and_verifies_frequency():
    text = generate_psi4_input(base_data(psi_calc_type="ts"))
    assert "opt_type ts" in text
    assert "full_hess_every 0" in text
    assert "optimize('b3lyp')" in text
    assert "frequency('b3lyp')" in text


def test_psi4_tddft_uses_td_target_states_and_tda():
    text = generate_psi4_input(base_data(psi_calc_type="tddft", nroots=15, psi_tda=True, psi_method="camb3lyp"))
    assert "tdscf_states 15" in text
    assert "tdscf_tda true" in text
    assert "energy('td-camb3lyp')" in text


def test_psi4_hf_tdscf_target():
    text = generate_psi4_input(base_data(psi_calc_type="tddft", psi_family="hf", psi_method="scf", nroots=8))
    assert "reference rhf" in text
    assert "energy('td-scf')" in text


def test_psi4_pcm_block_and_nonequilibrium_tdscf():
    text = generate_psi4_input(base_data(
        psi_calc_type="tddft",
        nroots=10,
        psi_solvation="pcm",
        psi_solvent="Water",
    ))
    assert "pcm true" in text
    assert "SolverType = IEFPCM" in text
    assert "Solvent = Water" in text
    assert "Nonequilibrium = True" in text


def test_psi4_mp2_density_fit_and_freeze_core():
    text = generate_psi4_input(base_data(
        psi_family="mp2",
        psi_method="mp2",
        psi_calc_type="sp",
        psi_freeze_core=True,
    ))
    assert "reference rhf" in text
    assert "freeze_core true" in text
    assert "mp2_type df" in text
    assert "energy('mp2')" in text


def test_psi4_rejects_mp2_pcm_in_wizard():
    try:
        generate_psi4_input(base_data(psi_family="mp2", psi_method="mp2", psi_solvation="pcm"))
    except ValueError as exc:
        assert "PCM" in str(exc)
    else:
        raise AssertionError("MP2 + PCM should be rejected by the conservative wizard")
