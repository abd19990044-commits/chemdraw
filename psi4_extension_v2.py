from __future__ import annotations

"""Scientifically conservative Psi4 v2 overlay.

Extends the existing dual ORCA/Psi4 wizard with options documented by Psi4.
The legacy ORCA workflow and Chemistry Drawing Bot are not modified here.
"""

import html
import io
from typing import Any

import psi4_extension as base


# Broaden only with method names/options documented by Psi4 drivers.
base.PSI4_CALC_TYPES.update({
    "Single Gradient": "gradient",
    "Hessian / Force Constants": "hessian",
})

base.PSI4_DFT_METHODS.update({
    "BLYP": "blyp",
    "B97M-V": "b97m-v",
    "ωB97X-V": "wb97x-v",
    "TPSS": "tpss",
    "TPSSh": "tpssh",
    "M06-L": "m06-l",
    "B97-3c": "b973c",
    "B3LYP-D3(BJ)": "b3lyp-d3bj2b",
    "PBE0-D3(BJ)": "pbe0-d3bj2b",
    "CAM-B3LYP-D3(BJ)": "cam-b3lyp-d3bj2b",
    "BP86-D3(BJ)": "bp86-d3bj2b",
    "BLYP-D3(BJ)": "blyp-d3bj2b",
    "B3LYP-D4": "b3lyp-d4",
    "PBE0-D4": "pbe0-d4",
})

base.PSI4_MP2_METHODS.update({
    "MP3": "mp3",
    "MP2.5": "mp2.5",
    "OMP2": "omp2",
    "OMP3": "omp3",
    "OMP2.5": "omp2.5",
})

base.PSI4_CC_METHODS.update({
    "CC2": "cc2",
    "CCD": "ccd",
})

base.PSI4_BASIS_MAP["def2"].update({
    "def2-SVPD": "def2-svpd",
    "def2-TZVPD": "def2-tzvpd",
    "def2-TZVPPD": "def2-tzvppd",
    "def2-QZVPP": "def2-qzvpp",
})
base.PSI4_BASIS_MAP["dunning"].update({
    "cc-pVQZ": "cc-pvqz",
    "aug-cc-pVQZ": "aug-cc-pvqz",
    "jun-cc-pVDZ": "jun-cc-pvdz",
    "jun-cc-pVTZ": "jun-cc-pvtz",
})

PSI4_GUESSES = {
    "Automatic (recommended)": "auto",
    "SAD": "sad",
    "SAP": "sap",
    "Core Hamiltonian": "core",
    "GWH": "gwh",
}

PSI4_SCF_MAXITER = {
    "50 iterations": "50",
    "100 iterations": "100",
    "200 iterations": "200",
}

PSI4_DFT_GRIDS = {
    "Psi4 default": "default",
    "Fine (99 radial × 590 Lebedev)": "fine",
    "Very fine (150 radial × 974 Lebedev)": "veryfine",
}

PSI4_GEOM_CONV = {
    "QCHEM (Psi4 default)": "QCHEM",
    "Gaussian": "GAU",
    "Gaussian Tight": "GAU_TIGHT",
    "Gaussian VeryTight": "GAU_VERYTIGHT",
    "Molpro": "MOLPRO",
}

PSI4_GEOM_MAXITER = {
    "50 steps": "50",
    "100 steps": "100",
    "200 steps": "200",
}

PSI4_OUTPUTS = {
    "No auxiliary wavefunction file": "none",
    "Molden orbitals": "molden",
    "Gaussian FCHK": "fchk",
    "Molden + FCHK": "both",
}


def _method_supports_derivative(method: str, calc: str) -> bool:
    # Psi4 can finite-difference many derivatives, but very expensive/fragile
    # combinations should not be advertised by the wizard as routine.
    m = method.lower()
    if calc in {"opt", "optfreq", "ts", "gradient", "hessian", "freq"}:
        if m == "ccsd(t)" and calc in {"opt", "optfreq", "ts"}:
            return False
    return True


def _validate_v2(d: dict[str, Any]) -> None:
    base._validate_psi4_data(d)
    calc = str(d.get("psi_calc_type", "sp"))
    method = str(d.get("psi_method", "b3lyp"))
    if not _method_supports_derivative(method, calc):
        raise ValueError(
            "This wizard does not generate CCSD(T) geometry optimizations/TS searches. "
            "Use a lower-level geometry and CCSD(T) single-point energy instead."
        )
    if str(d.get("psi_scf_guess", "auto")).lower() not in {"auto", "sad", "sap", "core", "gwh"}:
        raise ValueError("Unsupported SCF guess option.")
    if int(d.get("psi_scf_maxiter", 100)) < 10:
        raise ValueError("SCF max iterations must be at least 10.")
    if calc in {"opt", "optfreq", "ts"} and int(d.get("psi_geom_maxiter", 50)) < 1:
        raise ValueError("Geometry maximum iterations must be positive.")


def _driver_line(calc: str, target: str, want_wfn: bool) -> list[str]:
    suffix = ", return_wfn=True" if want_wfn else ""
    assign = "e, wfn = " if want_wfn else ""
    if calc == "sp" or calc == "tddft":
        return [f"{assign}energy('{target}'{suffix})"]
    if calc == "gradient":
        return [f"{('g, wfn = ' if want_wfn else '')}gradient('{target}'{suffix})"]
    if calc == "hessian":
        return [f"{('H, wfn = ' if want_wfn else '')}hessian('{target}'{suffix})"]
    if calc == "opt":
        return [f"{assign}optimize('{target}'{suffix})"]
    if calc == "freq":
        return [f"{assign}frequency('{target}'{suffix})"]
    if calc == "optfreq":
        lines = [f"optimize('{target}')"]
        lines.append(f"{assign}frequency('{target}'{suffix})")
        return lines
    if calc == "ts":
        lines = [f"optimize('{target}')"]
        lines.append(f"{assign}frequency('{target}'{suffix})")
        return lines
    raise ValueError(f"Unsupported Psi4 calculation type: {calc}")


def generate_psi4_input_v2(d: dict[str, Any]) -> str:
    _validate_v2(d)

    charge = int(d.get("charge", 0))
    mult = int(d.get("mult", 1))
    cores = int(d.get("cores", 4))
    memory_mb = int(d.get("psi_memory_mb", 4000))
    family = str(d.get("psi_family", "dft"))
    basis = str(d.get("psi_basis", "def2-tzvp"))
    scf_type = str(d.get("psi_scf_type", "df"))
    calc = str(d.get("psi_calc_type", "sp"))
    reference = base._psi4_reference(family, mult)
    target = base._psi4_target_method(d)

    lines = [
        f"memory {memory_mb} MB",
        f"set_num_threads({cores})",
        "",
        "molecule mol {",
        f"  {charge} {mult}",
    ]
    for line in str(d.get("coords", "")).strip().splitlines():
        if line.strip():
            lines.append("  " + line.strip())
    lines.extend([
        "  units angstrom",
        "  symmetry c1",
        "  no_reorient",
        "  no_com",
        "}",
        "",
        "set {",
        f"  basis {basis}",
        f"  reference {reference}",
        f"  scf_type {scf_type}",
        f"  guess {str(d.get('psi_scf_guess', 'auto')).lower()}",
        f"  maxiter {int(d.get('psi_scf_maxiter', 100))}",
    ])

    convergence = d.get("psi_convergence", "standard")
    if convergence == "tight":
        lines.extend(["  e_convergence 1e-8", "  d_convergence 1e-7"])
    elif convergence == "verytight":
        lines.extend(["  e_convergence 1e-10", "  d_convergence 1e-9"])

    if family in {"mp2", "cc"} and d.get("psi_freeze_core", True):
        lines.append("  freeze_core true")

    method_lower = str(d.get("psi_method", "")).lower()
    if family == "mp2" and scf_type == "df" and method_lower in {"mp2", "scs-mp2", "mp3", "mp2.5"}:
        lines.append("  mp2_type df")

    if family == "dft":
        grid = d.get("psi_dft_grid", "default")
        if grid == "fine":
            lines.extend(["  dft_radial_points 99", "  dft_spherical_points 590"])
        elif grid == "veryfine":
            lines.extend(["  dft_radial_points 150", "  dft_spherical_points 974"])

    if calc == "tddft":
        lines.append(f"  tdscf_states {int(d.get('nroots', 10))}")
        if d.get("psi_tda", False):
            lines.append("  tdscf_tda true")

    if calc in {"opt", "optfreq", "ts"}:
        lines.append(f"  g_convergence {d.get('psi_geom_convergence', 'QCHEM')}")
        lines.append(f"  geom_maxiter {int(d.get('psi_geom_maxiter', 50))}")
    if calc == "ts":
        lines.extend(["  opt_type ts", "  full_hess_every 0"])

    pcm_enabled = d.get("psi_solvation") == "pcm"
    if pcm_enabled:
        lines.extend(["  pcm true", "  pcm_scf_type total"])
        if family == "cc":
            lines.append("  pcm_cc_type pte")
    lines.append("}")

    if pcm_enabled:
        solvent = str(d.get("psi_solvent", "Water"))
        lines.extend([
            "",
            "pcm = {",
            "  Units = Angstrom",
            "  Medium {",
            "    SolverType = IEFPCM",
            f"    Solvent = {solvent}",
        ])
        if calc == "tddft":
            lines.append("    Nonequilibrium = True")
        lines.extend([
            "  }",
            "  Cavity {",
            "    RadiiSet = UFF",
            "    Type = GePol",
            "    Scaling = False",
            "    Area = 0.3",
            "    Mode = Implicit",
            "  }",
            "}",
        ])

    output_mode = str(d.get("psi_output", "none"))
    want_wfn = output_mode in {"molden", "fchk", "both"}
    lines.append("")
    lines.extend(_driver_line(calc, target, want_wfn))
    if want_wfn:
        stem = str(d.get("name", "molecule"))
        if output_mode in {"molden", "both"}:
            lines.append(f"molden(wfn, '{stem}.molden')")
        if output_mode in {"fchk", "both"}:
            lines.append(f"fchk(wfn, '{stem}.fchk')")
    if d.get("psi_print_variables", False):
        lines.append("print_variables()")
    lines.append("")
    return "\n".join(lines)


def install_psi4_extension_v2(core) -> None:
    # Make v1 installer use the expanded dictionaries and validated generator.
    base.generate_psi4_input = generate_psi4_input_v2
    base.install_psi4_extension(core)

    installed_callback = core.callback_handler
    installed_text = core.handle_text_input
    installed_start = core.start_command

    async def _after_advanced(query, ud):
        ud["state"] = "psi_waiting_for_charge"
        await query.edit_message_text("Psi4 — enter molecular charge (e.g. 0):")

    async def callback_handler(update, context):
        query = update.callback_query
        data = query.data or ""
        ud = context.user_data

        # Insert advanced-settings gate before v1 proceeds to charge.
        if data.startswith("psisolv_"):
            await query.answer()
            solvation = data.split("_", 1)[1]
            ud["psi_solvation"] = solvation
            if solvation == "none":
                await query.edit_message_text(
                    "Psi4 — advanced numerical settings?",
                    reply_markup=core.build_markup({"Recommended defaults": "defaults", "Configure advanced": "advanced"}, "psiadv", 1),
                )
                return
            await query.edit_message_text("Psi4 — select PCM solvent:", reply_markup=core.build_markup(base.PSI4_SOLVENTS, "psisolvnt", 2))
            return

        if data.startswith("psisolvnt_"):
            await query.answer()
            ud["psi_solvent"] = data.split("_", 1)[1]
            await query.edit_message_text(
                "Psi4 — advanced numerical settings?",
                reply_markup=core.build_markup({"Recommended defaults": "defaults", "Configure advanced": "advanced"}, "psiadv", 1),
            )
            return

        if data.startswith("psifc_") and ud.get("psi_family") == "mp2":
            await query.answer()
            ud["psi_freeze_core"] = data.endswith("yes")
            ud["psi_solvation"] = "none"
            await query.edit_message_text(
                "Psi4 — advanced numerical settings?",
                reply_markup=core.build_markup({"Recommended defaults": "defaults", "Configure advanced": "advanced"}, "psiadv", 1),
            )
            return

        if data == "psiadv_defaults":
            await query.answer()
            ud.setdefault("psi_scf_guess", "auto")
            ud.setdefault("psi_scf_maxiter", 100)
            ud.setdefault("psi_dft_grid", "default")
            ud.setdefault("psi_geom_convergence", "QCHEM")
            ud.setdefault("psi_geom_maxiter", 50)
            ud.setdefault("psi_output", "none")
            ud.setdefault("psi_print_variables", False)
            await _after_advanced(query, ud)
            return

        if data == "psiadv_advanced":
            await query.answer()
            await query.edit_message_text("Psi4 — SCF initial guess:", reply_markup=core.build_markup(PSI4_GUESSES, "psiguess", 1))
            return

        if data.startswith("psiguess_"):
            await query.answer()
            ud["psi_scf_guess"] = data.split("_", 1)[1]
            await query.edit_message_text("Psi4 — maximum SCF iterations:", reply_markup=core.build_markup(PSI4_SCF_MAXITER, "psimaxscf", 1))
            return

        if data.startswith("psimaxscf_"):
            await query.answer()
            ud["psi_scf_maxiter"] = int(data.split("_", 1)[1])
            if ud.get("psi_family") == "dft":
                await query.edit_message_text("Psi4 — DFT integration grid:", reply_markup=core.build_markup(PSI4_DFT_GRIDS, "psigrid", 1))
            elif ud.get("psi_calc_type") in {"opt", "optfreq", "ts"}:
                await query.edit_message_text("Psi4 — geometry convergence:", reply_markup=core.build_markup(PSI4_GEOM_CONV, "psigconv", 1))
            else:
                await query.edit_message_text("Psi4 — optional wavefunction output:", reply_markup=core.build_markup(PSI4_OUTPUTS, "psiout", 1))
            return

        if data.startswith("psigrid_"):
            await query.answer()
            ud["psi_dft_grid"] = data.split("_", 1)[1]
            if ud.get("psi_calc_type") in {"opt", "optfreq", "ts"}:
                await query.edit_message_text("Psi4 — geometry convergence:", reply_markup=core.build_markup(PSI4_GEOM_CONV, "psigconv", 1))
            else:
                await query.edit_message_text("Psi4 — optional wavefunction output:", reply_markup=core.build_markup(PSI4_OUTPUTS, "psiout", 1))
            return

        if data.startswith("psigconv_"):
            await query.answer()
            ud["psi_geom_convergence"] = data.split("_", 1)[1]
            await query.edit_message_text("Psi4 — maximum geometry steps:", reply_markup=core.build_markup(PSI4_GEOM_MAXITER, "psigmax", 1))
            return

        if data.startswith("psigmax_"):
            await query.answer()
            ud["psi_geom_maxiter"] = int(data.split("_", 1)[1])
            await query.edit_message_text("Psi4 — optional wavefunction output:", reply_markup=core.build_markup(PSI4_OUTPUTS, "psiout", 1))
            return

        if data.startswith("psiout_"):
            await query.answer()
            ud["psi_output"] = data.split("_", 1)[1]
            await query.edit_message_text("Psi4 — print QCVariables at the end?", reply_markup=core.build_markup({"No": "no", "Yes": "yes"}, "psivars", 2))
            return

        if data.startswith("psivars_"):
            await query.answer()
            ud["psi_print_variables"] = data.endswith("yes")
            await _after_advanced(query, ud)
            return

        await installed_callback(update, context)

    async def handle_text_input(update, context):
        # v1 text state machine already calls base.generate_psi4_input, now v2.
        await installed_text(update, context)

    async def start_command(update, context):
        # Keep the proven engine setup, but advertise the expanded validated surface.
        await installed_start(update, context)
        await update.message.reply_text(
            "Psi4 advanced mode also supports Gradient/Hessian, MP3/MP2.5/OMP methods, CC2/CCD, "
            "dispersion-corrected DFT aliases, diffuse basis sets, SCF guess/maxiter, DFT grids, "
            "geometry convergence presets, and optional Molden/FCHK output. Incompatible combinations are rejected rather than silently written."
        )

    core.callback_handler = callback_handler
    core.handle_text_input = handle_text_input
    core.start_command = start_command
    core.generate_psi4_input = generate_psi4_input_v2
    core.orca_logger.info("Psi4 v2 validated advanced options installed.")
