"""Regression tests for output geometry extraction and individual batch delivery."""

import asyncio
from types import SimpleNamespace

import pytest

from quantum_batch import (
    MAX_BATCH_FILES, extract_final_geometry, prepare_batch_inputs,
    read_structure,
)

WATER = "O 0 0 0\nH 0.7586 0 0.5043\nH -0.7586 0 0.5043"

GAUSSIAN = """
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          8           0        0.000000    0.000000    0.000000
      2          1           0        0.750000    0.000000    0.500000
      3          1           0       -0.750000    0.000000    0.500000
 ---------------------------------------------------------------------
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          8           0        0.100000    0.000000    0.000000
      2          1           0        0.750000    0.000000    0.500000
      3          1           0       -0.750000    0.000000    0.500000
 ---------------------------------------------------------------------
 Normal termination of Gaussian 16
"""

ORCA = """
CARTESIAN COORDINATES (ANGSTROEM)
---------------------------------
O    0.0000 0.0 0.0
H    0.7586 0.0 0.5043
H   -0.7586 0.0 0.5043

ORCA TERMINATED NORMALLY
"""


def test_extract_gaussian_last_frame_and_normal_termination():
    result = extract_final_geometry(GAUSSIAN)
    assert result.engine == "Gaussian"
    assert result.normal_termination
    assert len(result.xyz.splitlines()) == 3
    assert "0.1000000000" in result.xyz.splitlines()[0]


def test_orca_and_nonterminated_output_status():
    result = extract_final_geometry(ORCA)
    assert result.engine == "ORCA"
    assert result.normal_termination
    truncated = extract_final_geometry(ORCA.replace("ORCA TERMINATED NORMALLY", "Calculation interrupted"))
    assert not truncated.normal_termination
    assert "not a verified optimized minimum" in truncated.warning


def test_reject_unknown_output_and_binary():
    with pytest.raises(ValueError, match="No supported Cartesian"):
        extract_final_geometry("No structure found\n")
    with pytest.raises(ValueError, match="Binary"):
        read_structure(b"xyz" + bytes([0]), ".log")


def _settings(engine):
    base = dict(coords=WATER, name="water", charge=0, mult=1, cores=2)
    if engine == "orca":
        base.update(
            calc_type="sp", family="f_dft", theory="B3LYP",
            basis="def2-SVP", disp="none", ri_type="none",
            solv_model="none", ram=1200,
        )
    elif engine == "psi4":
        base.update(
            psi_calc_type="sp", psi_family="dft", psi_method="b3lyp",
            psi_basis="def2-svp", psi_memory_mb=1200,
            psi_scf_type="df", psi_solvation="none",
        )
    else:
        base.update(
            gauss_task="sp", gauss_method="B3LYP", gauss_basis="Def2SVP",
            gauss_dispersion="none", gauss_solvation="none",
            gauss_memory_mb=2000, gauss_disk_mb=2000,
        )
    return base


@pytest.mark.parametrize("engine,suffix", [
    ("orca", ".inp"), ("psi4", ".dat"), ("gaussian", ".gjf")
])
def test_batch_generates_individual_named_documents(engine, suffix):
    import app
    entries = [
        {"name": f"water_{i}", "xyz": WATER} for i in range(MAX_BATCH_FILES)
    ]
    files = prepare_batch_inputs(entries, _settings(engine), engine, app.core)
    assert len(files) == 20
    assert files[0][0] == "01_water_0" + suffix
    assert files[19][0] == "20_water_19" + suffix
    assert all(bytes([80, 75, 3, 4]) not in data for _, data in files)
    assert len({name for name, _ in files}) == 20


def test_invalid_charge_spin_fails_before_any_delivery():
    import app
    entries = [{"name": "water", "xyz": WATER}, {"name": "radical", "xyz": "O 0 0 0\nH 0 0 1"}]
    with pytest.raises(ValueError, match="Different charges/multiplicities"):
        prepare_batch_inputs(entries, _settings("orca"), "orca", app.core)
