"""Validated optional Gaussian 16 guided settings.

This module only adds well-scoped route keywords. It never executes Gaussian,
never infers the right electronic state, and does not generate input sections
required by Gen/GenECP, ONIOM, ModRedundant, Counterpoise or checkpoint restarts.
Use the existing /manual pathway for those jobs.
"""
from __future__ import annotations

from quantum_validation import bounded_int

# Telegram callback values must be short (64-byte callback_data limit).
SCF = {
    "Gaussian default": "default",
    "Tight convergence": "tight",
    "XQC rescue": "xqc",
    "Tight + XQC": "tightxqc",
}
SCF_CYCLES = {"Gaussian default": "default", "100 cycles": "100", "200 cycles": "200", "500 cycles": "500"}
GRID = {"Gaussian default": "default", "UltraFine": "ultrafine", "SuperFineGrid": "superfine"}
OPT_CONV = {"Gaussian default": "default", "Loose": "loose", "Tight": "tight", "VeryTight": "verytight"}
OPT_HESS = {"Default optimizer Hessian": "default", "CalcFC (costlier)": "calcfc"}
OPT_CYCLES = {"Gaussian default": "default", "100 steps": "100", "200 steps": "200"}
POP = {"Gaussian default": "default", "Full orbitals": "full", "Hirshfeld charges (DFT)": "hirshfeld"}
SYMMETRY = {"Gaussian default": "default", "NoSymm": "nosymm"}
GUESS = {"Gaussian default": "default", "Core guess": "core", "Huckel guess": "huckel", "Mix HOMO/LUMO (HF/DFT)": "mix"}
STABLE = {"Off": "none", "Check stability": "check", "Optimize wavefunction": "opt"}
TD_ROOT = {"Ground/default root 1": "1", "Root 2": "2", "Root 3": "3", "Root 5": "5", "Root 10": "10"}
NMR_MODE = {"GIAO (default)": "giao", "CSGT": "csgt"}
RAMAN = {"Gaussian default": "default", "NoRaman": "noraman", "Raman": "raman"}
TEMPERATURE = {"Gaussian default": "default", "298.15 K": "298.15", "310.15 K": "310.15"}
PRESSURE = {"Gaussian default": "default", "1.0 atm": "1.0", "0.5 atm": "0.5"}

POST_HF = {"MP2", "MP3", "CCSD", "CCSD(T)"}
HF_DFT_EXCEPT_HF = POST_HF | {"HF"}
OPT_TASKS = {"opt", "optfreq", "ts"}
FREQ_TASKS = {"freq", "optfreq", "ts"}

# Dict insertion order is also the Telegram wizard sequence.
FIELD_OPTIONS = {
    "scf": (SCF, "Gaussian — SCF convergence strategy:"),
    "scfcycles": (SCF_CYCLES, "Gaussian — maximum SCF iterations:"),
    "grid": (GRID, "Gaussian — DFT integration grid:"),
    "optconv": (OPT_CONV, "Gaussian — geometry convergence:"),
    "opthess": (OPT_HESS, "Gaussian — initial optimizer Hessian:"),
    "optcycles": (OPT_CYCLES, "Gaussian — maximum geometry steps:"),
    "pop": (POP, "Gaussian — population analysis:"),
    "symmetry": (SYMMETRY, "Gaussian — molecular symmetry handling:"),
    "guess": (GUESS, "Gaussian — initial SCF guess:"),
    "stable": (STABLE, "Gaussian — HF/DFT wavefunction stability:"),
    "tdroot": (TD_ROOT, "Gaussian — TD-DFT target excited-state root:"),
    "nmrmode": (NMR_MODE, "Gaussian — NMR shielding formalism:"),
    "raman": (RAMAN, "Gaussian — frequency Raman calculation:"),
    "temperature": (TEMPERATURE, "Gaussian — thermochemistry temperature:"),
    "pressure": (PRESSURE, "Gaussian — thermochemistry pressure:"),
}

def is_dft(method: str) -> bool:
    return method not in POST_HF | {"HF"}

def active_fields(data: dict) -> list[str]:
    method = str(data.get("gauss_method", "B3LYP"))
    task = str(data.get("gauss_task", "sp"))
    names = ["scf", "scfcycles"]
    if is_dft(method):
        names.append("grid")
    if task in OPT_TASKS:
        names.extend(["optconv", "opthess", "optcycles"])
    names.extend(["pop", "symmetry", "guess"])
    if method not in POST_HF:
        names.append("stable")
    if task == "td":
        names.append("tdroot")
    if task == "nmr":
        names.append("nmrmode")
    if task in FREQ_TASKS:
        names.extend(["raman", "temperature", "pressure"])
    return names

def options_for(data: dict, field: str) -> dict[str, str]:
    if field not in active_fields(data):
        raise ValueError("This Gaussian setting is not available for the selected task/method.")
    options = FIELD_OPTIONS[field][0]
    if field == "pop" and not is_dft(str(data.get("gauss_method", "B3LYP"))):
        return {k: v for k, v in options.items() if v != "hirshfeld"}
    if field == "guess" and str(data.get("gauss_method", "B3LYP")) in POST_HF:
        return {k: v for k, v in options.items() if v != "mix"}
    if field == "raman" and str(data.get("gauss_method", "B3LYP")) in POST_HF:
        return {k: v for k, v in options.items() if v != "raman"}
    return options

def validate_and_route(data: dict) -> list[str]:
    """Return optional Gaussian route additions, rejecting stale/invalid state.

    All settings are opt-in. Missing options retain Gaussian's own defaults
    and preserve the original minimal guided input.
    """
    method = str(data.get("gauss_method", "B3LYP"))
    task = str(data.get("gauss_task", "sp"))
    active = active_fields(data)
    values: dict[str, str] = {}
    for field in FIELD_OPTIONS:
        key = "gauss_" + field
        if key not in data:
            values[field] = "default" if field not in {"stable", "tdroot", "nmrmode"} else {"stable": "none", "tdroot": "1", "nmrmode": "giao"}[field]
            continue
        if field not in active:
            raise ValueError(f"Gaussian option {field} is not applicable to {method}/{task}.")
        value = str(data[key]).lower()
        if value not in options_for(data, field).values():
            raise ValueError(f"Unsupported Gaussian {field} setting.")
        values[field] = value

    route = []
    scf = values["scf"]
    cycle = values["scfcycles"]
    scf_tokens = {"default": [], "tight": ["Tight"], "xqc": ["XQC"], "tightxqc": ["Tight", "XQC"]}[scf]
    if cycle != "default":
        bounded_int(cycle, "SCF MaxCycle", 1, 500)
        scf_tokens.append("MaxCycle=" + cycle)
    if scf_tokens:
        route.append("SCF=" + (scf_tokens[0] if len(scf_tokens) == 1 else "(" + ",".join(scf_tokens) + ")"))

    grid = values["grid"]
    if grid != "default":
        route.append("Integral=" + {"ultrafine": "UltraFine", "superfine": "SuperFineGrid"}[grid])

    if task in OPT_TASKS:
        opt_tokens = ["TS", "CalcFC"] if task == "ts" else []
        conv = values["optconv"]
        if conv != "default":
            opt_tokens.append({"loose": "Loose", "tight": "Tight", "verytight": "VeryTight"}[conv])
        if values["opthess"] == "calcfc" and "CalcFC" not in opt_tokens:
            opt_tokens.append("CalcFC")
        optsteps = values["optcycles"]
        if optsteps != "default":
            bounded_int(optsteps, "Optimization MaxCycles", 1, 200)
            opt_tokens.append("MaxCycles=" + optsteps)
        # This string replaces the existing Opt keyword rather than duplicating it.
        opt_keyword = "Opt"
        if opt_tokens:
            opt_keyword += "=" + (opt_tokens[0] if len(opt_tokens) == 1 else "(" + ",".join(opt_tokens) + ")")
        route.append("@opt:" + opt_keyword)

    if values["pop"] != "default":
        route.append("Pop=" + {"full": "Full", "hirshfeld": "Hirshfeld"}[values["pop"]])
    if values["symmetry"] == "nosymm":
        route.append("NoSymm")
    if values["guess"] != "default":
        route.append("Guess=" + {"core": "Core", "huckel": "Huckel", "mix": "Mix"}[values["guess"]])
    if values["stable"] != "none":
        route.append("Stable" if values["stable"] == "check" else "Stable=Opt")

    if task == "td":
        root = bounded_int(values["tdroot"], "TD target root", 1, 200)
        nstates = bounded_int(data.get("nroots", 10), "TD NStates", 1, 200)
        if root > nstates:
            raise ValueError("TD target Root must not exceed NStates.")
        if root != 1:
            route.append("@tdroot:" + str(root))

    if task == "nmr" and values["nmrmode"] == "csgt":
        route.append("@nmr:NMR=CSGT")
    if task in FREQ_TASKS:
        if values["raman"] != "default":
            route.append("@freq:Freq=" + ("Raman" if values["raman"] == "raman" else "NoRaman"))
        if values["temperature"] != "default":
            route.append("Temperature=" + values["temperature"])
        if values["pressure"] != "default":
            route.append("Pressure=" + values["pressure"])
    return route
