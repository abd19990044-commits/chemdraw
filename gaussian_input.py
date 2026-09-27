"""Conservative Gaussian 16 Linux input generation for the guided bot.

Expert-only keywords, mixed basis/ECP, checkpoint restarts, and Link1 jobs are
available through the separate manual-input path, without rewriting user text.
"""

from __future__ import annotations

from quantum_validation import (
    MAX_GAUSSIAN_DISK_MB,
    bounded_int,
    checked_molecule,
    safe_stem,
)


TASKS = {
    "Single point energy": "sp",
    "Geometry optimization": "opt",
    "Frequencies / thermochemistry": "freq",
    "Optimization + frequencies": "optfreq",
    "Transition state + frequencies": "ts",
    "TD-DFT / UV-Vis": "td",
    "NMR shielding (GIAO)": "nmr",
}
METHODS = {
    "B3LYP": "B3LYP",
    "PBE0 (Gaussian PBE1PBE)": "PBE1PBE",
    "CAM-B3LYP": "CAM-B3LYP",
    "M06-2X": "M062X",
    "ωB97X-D (built-in dispersion)": "wB97XD",
    "Hartree-Fock": "HF",
    "MP2": "MP2",
    "CCSD(T) — single point only": "CCSD(T)",
}
BASIS = {
    "6-31G(d)": "6-31G(d)",
    "6-31+G(d,p)": "6-31+G(d,p)",
    "6-311++G(d,p)": "6-311++G(d,p)",
    "def2-SVP": "Def2SVP",
    "def2-TZVP": "Def2TZVP",
    "cc-pVDZ": "cc-pVDZ",
    "aug-cc-pVDZ": "aug-cc-pVDZ",
}
DISPERSION = {"None / functional default": "none", "D3(BJ)": "GD3BJ"}
SOLVATION = {"Gas phase": "none", "IEFPCM": "IEFPCM", "SMD": "SMD"}
SOLVENTS = {
    "Water": "Water",
    "Methanol": "Methanol",
    "Ethanol": "Ethanol",
    "Acetonitrile": "Acetonitrile",
    "Acetone": "Acetone",
    "Dichloromethane": "Dichloromethane",
    "Chloroform": "Chloroform",
    "DMSO": "DMSO",
    "THF": "THF",
    "Toluene": "Toluene",
}
ACCURACY = {"Standard": "standard", "Tight SCF + UltraFine grid": "tight"}


def allowed_tasks(method: str) -> dict[str, str]:
    if method == "CCSD(T)":
        return {"Single point energy": "sp"}
    if method == "MP2":
        return {
            label: task
            for label, task in TASKS.items()
            if task in {"sp", "opt", "freq", "optfreq"}
        }
    if method == "HF":
        return {label: task for label, task in TASKS.items() if task != "td"}
    return TASKS


def generate_gaussian_16_input(data: dict) -> str:
    coords, charge, mult = checked_molecule(data)
    task = str(data.get("gauss_task", "sp"))
    method = str(data.get("gauss_method", "B3LYP"))
    basis = str(data.get("gauss_basis", "Def2TZVP"))
    dispersion = str(data.get("gauss_dispersion", "none"))
    solvation = str(data.get("gauss_solvation", "none"))
    solvent = str(data.get("gauss_solvent", "Water"))
    accuracy = str(data.get("gauss_accuracy", "standard"))
    if method not in METHODS.values() or basis not in BASIS.values():
        raise ValueError(
            "Choose a supported Gaussian method and basis, or use manual input."
        )
    if task not in allowed_tasks(method).values():
        raise ValueError(
            f"Gaussian {task} is not offered for {method}; use manual input for expert jobs."
        )
    if dispersion not in DISPERSION.values() or (
        dispersion != "none" and method not in {"B3LYP", "PBE1PBE"}
    ):
        raise ValueError(
            "D3(BJ) is offered only for the validated B3LYP and PBE1PBE presets."
        )
    if solvation not in SOLVATION.values() or solvent not in SOLVENTS.values():
        raise ValueError("Choose a supported Gaussian solvent model and solvent.")
    if method in {"MP2", "CCSD(T)"} and solvation != "none":
        raise ValueError(
            "The guided post-HF presets use gas phase; specify expert solvent jobs manually."
        )
    if accuracy not in ACCURACY.values():
        raise ValueError("Invalid accuracy preset.")

    cores = bounded_int(data.get("cores", 4), "CPU cores", 1, 128)
    memory = bounded_int(
        data.get("gauss_memory_mb", 4000), "Total memory (MB)", 256, 512000
    )
    disk = bounded_int(
        data.get("gauss_disk_mb", MAX_GAUSSIAN_DISK_MB),
        "MaxDisk (MB)",
        100,
        MAX_GAUSSIAN_DISK_MB,
    )
    stem = safe_stem(data.get("name", "molecule"))
    title = f"{stem} | {method}/{basis} | {task}"
    route = [f"{method}/{basis}"]
    route.extend(
        {
            "sp": [],
            "opt": ["Opt"],
            "freq": ["Freq"],
            "optfreq": ["Opt", "Freq"],
            "ts": ["Opt=(TS,CalcFC)", "Freq"],
            "td": [
                f"TD=(NStates={bounded_int(data.get('nroots', 10), 'Excited states', 1, 200)})"
            ],
            "nmr": ["NMR=GIAO"],
        }[task]
    )
    if dispersion != "none":
        route.append(f"EmpiricalDispersion={dispersion}")
    if solvation != "none":
        route.append(f"SCRF=({solvation},Solvent={solvent})")
    if accuracy == "tight":
        route.append("SCF=(Tight,XQC)")
        if method not in {"HF", "MP2", "CCSD(T)"}:
            route.append("Integral=UltraFine")
    route.append(f"MaxDisk={disk}MB")
    return (
        f"%NProcShared={cores}\n%Mem={memory}MB\n%Chk={stem}.chk\n"
        f"#p {' '.join(route)}\n\n{title}\n\n{charge} {mult}\n"
        f"{coords}\n\n"
    )
