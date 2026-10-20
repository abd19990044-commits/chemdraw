import asyncio
from types import SimpleNamespace

import pytest

import gaussian_catalog as catalog
import gaussian_advanced as advanced
import gaussian_input as gaussian

WATER = "O 0 0 0\nH 0.7586 0 0.5043\nH -0.7586 0 0.5043"

def base_data(**kwargs):
    d = dict(coords=WATER, name="water", charge=0, mult=1,
             gauss_task="sp", gauss_method="B3LYP", gauss_basis="Def2TZVP",
             cores=4, gauss_memory_mb=4000, gauss_disk_mb=5000,
             gauss_dispersion="none", gauss_solvation="none", gauss_accuracy="standard")
    d.update(kwargs)
    return d

def test_catalog_is_broad_and_method_task_limited():
    assert len(set(catalog.METHODS.values())) >= 35
    assert len(set(catalog.BASIS.values())) >= 25
    assert "Def2QZVP" in catalog.BASIS.values()
    assert "aug-cc-pVTZ" in catalog.BASIS.values()
    assert gaussian.allowed_tasks("CCSD(T)") == {"Single point energy": "sp"}
    assert "td" not in gaussian.allowed_tasks("HF").values()

def test_scf_grid_and_population_are_integrated_once():
    inp = gaussian.generate_gaussian_16_input(base_data(
        gauss_scf="tightxqc", gauss_scfcycles="200", gauss_grid="superfine",
        gauss_pop="hirshfeld", gauss_symmetry="nosymm", gauss_stable="check"))
    assert "SCF=(Tight,XQC,MaxCycle=200)" in inp
    assert inp.count("SCF=") == 1
    assert "Integral=SuperFineGrid" in inp
    assert "Pop=Hirshfeld" in inp
    assert "NoSymm" in inp and "Stable" in inp

def test_optimization_advanced_does_not_duplicate_opt():
    inp = gaussian.generate_gaussian_16_input(base_data(
        gauss_task="optfreq", gauss_optconv="verytight", gauss_opthess="calcfc",
        gauss_optcycles="100", gauss_raman="noraman"))
    assert "Opt=(VeryTight,CalcFC,MaxCycles=100)" in inp
    assert "Freq=NoRaman" in inp
    assert inp.count(" Opt") == 1

def test_td_root_invalid_above_nstates():
    with pytest.raises(ValueError, match="Root must not exceed NStates"):
        gaussian.generate_gaussian_16_input(base_data(gauss_task="td", nroots=2, gauss_tdroot="3"))

def test_unknown_functionals_restricted_and_no_section_injection():
    with pytest.raises(ValueError, match="method"):
        gaussian.generate_gaussian_16_input(base_data(gauss_method="CustomUnknown"))
    with pytest.raises(ValueError):
        catalog.custom_keyword("B3LYP #p Force", "method")
    with pytest.raises(ValueError):
        catalog.custom_keyword("GenECP", "basis")
    inp = gaussian.generate_gaussian_16_input(base_data(gauss_method="CustomUnknown", gauss_custom_method="CustomUnknown"))
    assert "#p CustomUnknown/Def2TZVP" in inp

def test_existing_guided_defaults_not_perturbed():
    inp = gaussian.generate_gaussian_16_input(base_data(gauss_task="optfreq",gauss_method="B3LYP",
        gauss_accuracy="tight"))
    assert "SCF=(Tight,XQC)" in inp
    assert "Integral=UltraFine" in inp
    assert "Opt Freq" in inp
