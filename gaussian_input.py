"""Conservative Gaussian 16 Linux input generation for the guided bot.

Expert-only keywords, mixed basis/ECP, checkpoint restarts, and Link1 jobs are
available through the separate manual-input path, without rewriting user text.
"""

from __future__ import annotations

import gaussian_catalog as catalog
import gaussian_advanced as advanced

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
    "Gradient / atomic forces": "force",
    "Static polarizability": "polar",
}
METHODS = catalog.METHODS
BASIS = catalog.BASIS

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
    # Advanced post-HF and double hybrids are conservatively single-point only
    # in the guided workflow. More demanding derivative jobs use /manual.
    restricted = {
        "CCSD(T)", "CCSD", "MP3", "QCISD", "QCISD(T)",
        "B2PLYP", "mPW2PLYP", "PBE0DH", "PBEQIDH",
    }
    if method in restricted:
        return {"Single point energy": "sp"}
    if method == "MP2":
        return {k: v for k, v in TASKS.items() if v in {"sp", "opt", "freq", "optfreq", "force"}}
    if method == "HF":
        return {k: v for k, v in TASKS.items() if v != "td"}
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
    custom_method = data.get("gauss_custom_method") == method
    custom_basis = data.get("gauss_custom_basis") == basis
    if method not in METHODS.values() and not custom_method:
        raise ValueError("Choose a supported Gaussian method or enter a custom method keyword.")
    if basis not in BASIS.values() and not custom_basis:
        raise ValueError("Choose a supported basis or enter a custom basis keyword.")
    if custom_method:
        catalog.custom_keyword(method, "method")
        if task != "sp":
            raise ValueError("Custom Gaussian methods are single-point only in the guided mode; use /manual for derivatives.")
    if custom_basis:
        catalog.custom_keyword(basis, "basis")
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
    if method in {"MP2", "MP3", "CCSD", "CCSD(T)", "QCISD", "QCISD(T)"} and solvation != "none":
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
            "force": ["Force"],
            "polar": ["Polar"],
        }[task]
    )
    if dispersion != "none":
        route.append(f"EmpiricalDispersion={dispersion}")
    if solvation != "none":
        route.append(f"SCRF=({solvation},Solvent={solvent})")
    if accuracy == "tight":
        route.append("SCF=(Tight,XQC)")
        if method not in advanced.POST_HF and method != "HF":
            route.append("Integral=UltraFine")
    if any("gauss_" + fld in data for fld in advanced.FIELD_OPTIONS):
        for opt in advanced.validate_and_route(data):
            if opt.startswith("@opt:"):
                keyword = opt.split(":", 1)[1]
                route = [item for item in route if item != "Opt" and not item.startswith("Opt=")]
                route.insert(1, keyword)
            elif opt.startswith("@freq:"):
                keyword = opt.split(":", 1)[1]
                route = [item for item in route if item != "Freq" and not item.startswith("Freq=")]
                route.insert(2 if task in {"optfreq", "ts"} else 1, keyword)
            elif opt.startswith("@nmr:"):
                keyword = opt.split(":", 1)[1]
                route = [item for item in route if not item.startswith("NMR=")]
                route.insert(1, keyword)
            elif opt.startswith("@tdroot:"):
                nstates = bounded_int(data.get("nroots", 10), "Excited states", 1, 200)
                root = bounded_int(opt.split(":", 1)[1], "TD root", 1, nstates)
                route = [
                    f"TD=(NStates={nstates},Root={root})" if item.startswith("TD=(") else item
                    for item in route
                ]
            elif opt.startswith(("SCF=", "Integral=")):
                key = opt.split("=", 1)[0] + "="
                route = [item for item in route if not item.startswith(key)]
                route.append(opt)
            else:
                route.append(opt)
    route.append(f"MaxDisk={disk}MB")
    return (
        f"%NProcShared={cores}\n%Mem={memory}MB\n%Chk={stem}.chk\n"
        f"#p {' '.join(route)}\n\n{title}\n\n{charge} {mult}\n"
        f"{coords}\n\n"
    )
