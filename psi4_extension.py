from __future__ import annotations

"""Psi4 input generation and ORCA-bot dual-engine wizard overlay.

This module extends the existing ORCA webhook bot without modifying legacy_app.py.
The original ORCA workflow remains available unchanged after the user selects ORCA;
Psi4 uses a separate, scientifically appropriate Psithon workflow and writes .dat
input files.
"""

import html
import io
from typing import Any


PSI4_CALC_TYPES = {
    "Single Point Energy": "sp",
    "Geometry Optimization": "opt",
    "Optimization + Frequencies": "optfreq",
    "Frequencies / Thermochemistry": "freq",
    "Transition State Search": "ts",
    "TD-DFT / UV-Vis": "tddft",
}

PSI4_FAMILIES = {
    "DFT": "dft",
    "Hartree-Fock": "hf",
    "MP2 / SCS-MP2": "mp2",
    "Coupled Cluster": "cc",
}

PSI4_DFT_METHODS = {
    "B3LYP": "b3lyp",
    "PBE0": "pbe0",
    "CAM-B3LYP": "cam-b3lyp",
    "ωB97X": "wb97x",
    "ωB97X-D": "wb97x-d",
    "ωB97M-V": "wb97m-v",
    "M06-2X": "m06-2x",
    "PBE": "pbe",
    "BP86": "bp86",
}

PSI4_MP2_METHODS = {
    "MP2": "mp2",
    "SCS-MP2": "scs-mp2",
}

PSI4_CC_METHODS = {
    "CCSD": "ccsd",
    "CCSD(T)": "ccsd(t)",
}

PSI4_BASIS_FAMILIES = {
    "Ahlrichs def2": "def2",
    "Dunning cc-pVXZ": "dunning",
    "Pople": "pople",
}

PSI4_BASIS_MAP = {
    "def2": {
        "def2-SVP": "def2-svp",
        "def2-TZVP": "def2-tzvp",
        "def2-TZVPP": "def2-tzvpp",
        "def2-QZVP": "def2-qzvp",
    },
    "dunning": {
        "cc-pVDZ": "cc-pvdz",
        "cc-pVTZ": "cc-pvtz",
        "aug-cc-pVDZ": "aug-cc-pvdz",
        "aug-cc-pVTZ": "aug-cc-pvtz",
    },
    "pople": {
        "6-31G(d)": "6-31g(d)",
        "6-31+G(d,p)": "6-31+g(d,p)",
        "6-311G(d,p)": "6-311g(d,p)",
        "6-311++G(d,p)": "6-311++g(d,p)",
    },
}

PSI4_SCF_TYPES = {
    "Density fitting (DF) — recommended": "df",
    "Conventional PK": "pk",
    "Direct integrals": "direct",
}

PSI4_CONVERGENCE = {
    "Standard": "standard",
    "Tight": "tight",
    "Very tight": "verytight",
}

PSI4_SOLVATION = {
    "Gas phase": "none",
    "PCM / PCMSolver": "pcm",
}

PSI4_SOLVENTS = {
    "Water": "Water",
    "Ethanol": "Ethanol",
    "Methanol": "Methanol",
    "Acetone": "Acetone",
    "Chloroform": "Chloroform",
    "Dichloromethane": "Dichloromethane",
    "DMSO": "Dimethylsulfoxide",
    "THF": "Tetrahydrofuran",
    "Toluene": "Toluene",
}


def _psi4_reference(family: str, multiplicity: int) -> str:
    open_shell = int(multiplicity) != 1
    if family == "dft":
        return "uks" if open_shell else "rks"
    return "uhf" if open_shell else "rhf"


def _psi4_target_method(d: dict[str, Any]) -> str:
    family = d.get("psi_family", "dft")
    method = str(d.get("psi_method", "b3lyp")).strip().lower()
    if d.get("psi_calc_type") == "tddft":
        if family == "hf":
            return "td-scf"
        if family != "dft":
            raise ValueError("TD-SCF in this wizard is restricted to HF or DFT references.")
        return f"td-{method}"
    if family == "hf":
        return "scf"
    return method


def _validate_psi4_data(d: dict[str, Any]) -> None:
    if not str(d.get("coords", "")).strip():
        raise ValueError("Molecular coordinates are missing.")
    charge = int(d.get("charge", 0))
    mult = int(d.get("mult", 1))
    if mult < 1:
        raise ValueError("Multiplicity must be a positive integer.")
    if charge < -20 or charge > 20:
        raise ValueError("Charge is outside the supported wizard range (-20..20).")
    cores = int(d.get("cores", 1))
    memory_mb = int(d.get("psi_memory_mb", 2000))
    if cores < 1 or cores > 256:
        raise ValueError("Thread count must be between 1 and 256.")
    if memory_mb < 256:
        raise ValueError("Psi4 total memory must be at least 256 MB.")
    calc = d.get("psi_calc_type", "sp")
    family = d.get("psi_family", "dft")
    if calc == "tddft" and family not in {"dft", "hf"}:
        raise ValueError("TD-DFT/TD-HF requires the DFT or HF family in this wizard.")
    if d.get("psi_solvation") == "pcm" and family == "mp2":
        raise ValueError("PCM is not enabled for MP2 in this wizard because PCMSolver support is not general for MP2.")


def generate_psi4_input(d: dict[str, Any]) -> str:
    """Generate a Psi4 Psithon input file suitable for the ``.dat`` extension."""
    _validate_psi4_data(d)

    charge = int(d.get("charge", 0))
    mult = int(d.get("mult", 1))
    cores = int(d.get("cores", 4))
    memory_mb = int(d.get("psi_memory_mb", 4000))
    family = str(d.get("psi_family", "dft"))
    basis = str(d.get("psi_basis", "def2-tzvp"))
    scf_type = str(d.get("psi_scf_type", "df"))
    calc = str(d.get("psi_calc_type", "sp"))
    reference = _psi4_reference(family, mult)
    target = _psi4_target_method(d)

    lines: list[str] = []
    lines.append(f"memory {memory_mb} MB")
    lines.append(f"set_num_threads({cores})")
    lines.append("")
    lines.append("molecule mol {")
    lines.append(f"  {charge} {mult}")
    for coord_line in str(d.get("coords", "")).strip().splitlines():
        if coord_line.strip():
            lines.append("  " + coord_line.strip())
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
    ])

    convergence = d.get("psi_convergence", "standard")
    if convergence == "tight":
        lines.extend(["  e_convergence 1e-8", "  d_convergence 1e-7"])
    elif convergence == "verytight":
        lines.extend(["  e_convergence 1e-10", "  d_convergence 1e-9"])

    if family in {"mp2", "cc"} and d.get("psi_freeze_core", True):
        lines.append("  freeze_core true")
    if family == "mp2" and scf_type == "df":
        lines.append("  mp2_type df")

    if calc == "tddft":
        lines.append(f"  tdscf_states {int(d.get('nroots', 10))}")
        if d.get("psi_tda", False):
            lines.append("  tdscf_tda true")

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

    lines.append("")
    if calc == "sp":
        lines.append(f"energy('{target}')")
    elif calc == "opt":
        lines.append(f"optimize('{target}')")
    elif calc == "optfreq":
        lines.append(f"optimize('{target}')")
        lines.append(f"frequency('{target}')")
    elif calc == "freq":
        lines.append(f"frequency('{target}')")
    elif calc == "ts":
        lines.append(f"optimize('{target}')")
        lines.append(f"frequency('{target}')")
    elif calc == "tddft":
        lines.append(f"energy('{target}')")
    else:
        raise ValueError(f"Unsupported Psi4 calculation type: {calc}")

    lines.append("")
    return "\n".join(lines)


def _engine_markup(core):
    return core.build_markup({"ORCA 6.1": "orca", "Psi4": "psi4"}, "engine", 2)


def _psi_task_markup(core):
    return core.build_markup(PSI4_CALC_TYPES, "psicalc", 2)


def _continue_after_solvent(core, query, context):
    context.user_data["state"] = "psi_waiting_for_charge"
    return query.edit_message_text("Psi4 — enter molecular charge (e.g. 0):")


def install_psi4_extension(core) -> None:
    """Patch only the ORCA/webhook bot entry points with a Psi4 engine choice."""
    original_start = core.start_command
    original_handle_name = core.handle_name
    original_handle_file = core.handle_file
    original_callback = core.callback_handler
    original_text = core.handle_text_input

    async def start_command(update, context):
        context.user_data.clear()
        text = (
            "🧮 Quantum Chemistry Input Generator\n\n"
            "Generates scientifically structured input files for:\n"
            "• ORCA 6.1  → .inp\n"
            "• Psi4      → .dat (Psithon)\n\n"
            "Input geometry:\n"
            "1. Send # followed by an English compound name, e.g. #benzene\n"
            "2. Or upload XYZ, MOL, or SDF coordinates.\n\n"
            "After the geometry is loaded, choose ORCA or Psi4 and configure the calculation step-by-step. "
            "Psi4 supports SP, optimization, optimization+frequencies, frequencies/thermochemistry, TS search, and TD-DFT/UV-Vis."
        )
        await update.message.reply_text(text)

    async def _finish_geometry(update, context, coords, name, image_data=None, props_text=None):
        if image_data:
            await update.message.reply_photo(photo=image_data)
        if props_text:
            await update.message.reply_text(props_text, parse_mode="HTML")
        context.user_data["coords"] = coords
        context.user_data["name"] = core.sanitize_filename(name)
        context.user_data["state"] = "waiting_for_engine"
        await update.message.reply_text("Select quantum-chemistry engine:", reply_markup=_engine_markup(core))

    async def handle_name(update, context):
        name = update.message.text[1:].strip()
        if not name:
            await update.message.reply_text("Please send a name after #, e.g. #benzene")
            return
        wait_msg = await update.message.reply_text(f"⏳ Processing `{name}` and loading coordinates...")
        coords, image_data, props_text, err = await core.fetch_compound_async(name)
        await wait_msg.delete()
        if not coords:
            await update.message.reply_text(f"❌ Error: {err}")
            return
        await _finish_geometry(update, context, coords, name, image_data, props_text)

    async def handle_file(update, context):
        document = update.message.document
        ext = core.os.path.splitext(document.file_name)[1].lower()
        if ext not in {".sdf", ".mol", ".xyz"}:
            # Keep legacy behavior for any format not owned by this extension.
            await original_handle_file(update, context)
            return
        wait_msg = await update.message.reply_text("⏳ Processing uploaded coordinates...")
        try:
            file = await context.bot.get_file(document.file_id)
            raw_bytes = await file.download_as_bytes()
            coords, _, _, err = core.get_xyz_from_file(raw_bytes, ext)
            if not coords:
                await wait_msg.edit_text(f"❌ Failed to parse file: {err}")
                return
            await wait_msg.delete()
            name = core.os.path.splitext(document.file_name)[0]
            await _finish_geometry(update, context, coords, name)
        except Exception as exc:
            await wait_msg.edit_text(f"❌ Error processing file: {exc}")

    async def callback_handler(update, context):
        query = update.callback_query
        data = query.data or ""
        ud = context.user_data

        if data == "engine_orca":
            await query.answer()
            ud["engine"] = "orca"
            ud.pop("state", None)
            await query.edit_message_text("ORCA 6.1 — Step 1: Task:", reply_markup=core.build_markup(core.CALC_TYPES, "calc"))
            return
        if data == "engine_psi4":
            await query.answer()
            ud["engine"] = "psi4"
            ud.pop("state", None)
            await query.edit_message_text("Psi4 — Step 1: Calculation task:", reply_markup=_psi_task_markup(core))
            return

        if not data.startswith("psi"):
            await original_callback(update, context)
            return

        await query.answer()
        try:
            if data.startswith("psicalc_"):
                calc = data.split("_", 1)[1]
                ud["psi_calc_type"] = calc
                families = PSI4_FAMILIES.copy()
                if calc == "tddft":
                    families = {"DFT": "dft", "Hartree-Fock": "hf"}
                await query.edit_message_text("Psi4 — Step 2: Theory family:", reply_markup=core.build_markup(families, "psifam", 2))

            elif data.startswith("psifam_"):
                fam = data.split("_", 1)[1]
                ud["psi_family"] = fam
                if fam == "dft":
                    await query.edit_message_text("Psi4 — select DFT functional:", reply_markup=core.build_markup(PSI4_DFT_METHODS, "psimeth", 2))
                elif fam == "hf":
                    ud["psi_method"] = "scf"
                    await query.edit_message_text("Psi4 — Step 3: Basis family:", reply_markup=core.build_markup(PSI4_BASIS_FAMILIES, "psibfam", 2))
                elif fam == "mp2":
                    await query.edit_message_text("Psi4 — select MP2 method:", reply_markup=core.build_markup(PSI4_MP2_METHODS, "psimeth", 2))
                else:
                    await query.edit_message_text("Psi4 — select coupled-cluster method:", reply_markup=core.build_markup(PSI4_CC_METHODS, "psimeth", 2))

            elif data.startswith("psimeth_"):
                ud["psi_method"] = data.split("_", 1)[1]
                await query.edit_message_text("Psi4 — Step 3: Basis family:", reply_markup=core.build_markup(PSI4_BASIS_FAMILIES, "psibfam", 2))

            elif data.startswith("psibfam_"):
                fam = data.split("_", 1)[1]
                await query.edit_message_text("Psi4 — select orbital basis:", reply_markup=core.build_markup(PSI4_BASIS_MAP[fam], "psibasis", 2))

            elif data.startswith("psibasis_"):
                ud["psi_basis"] = data.split("_", 1)[1]
                await query.edit_message_text("Psi4 — SCF integral algorithm:", reply_markup=core.build_markup(PSI4_SCF_TYPES, "psiscf", 1))

            elif data.startswith("psiscf_"):
                ud["psi_scf_type"] = data.split("_", 1)[1]
                await query.edit_message_text("Psi4 — convergence level:", reply_markup=core.build_markup(PSI4_CONVERGENCE, "psiconv", 1))

            elif data.startswith("psiconv_"):
                ud["psi_convergence"] = data.split("_", 1)[1]
                if ud.get("psi_family") in {"mp2", "cc"}:
                    await query.edit_message_text("Psi4 — freeze core orbitals?", reply_markup=core.build_markup({"Yes (recommended for routine valence correlation)": "yes", "No (all-electron correlation)": "no"}, "psifc", 1))
                else:
                    await query.edit_message_text("Psi4 — solvation model:", reply_markup=core.build_markup(PSI4_SOLVATION, "psisolv", 1))

            elif data.startswith("psifc_"):
                ud["psi_freeze_core"] = data.endswith("yes")
                if ud.get("psi_family") == "mp2":
                    ud["psi_solvation"] = "none"
                    ud["state"] = "psi_waiting_for_charge"
                    await query.edit_message_text("Psi4 — MP2 wizard uses gas phase here. Enter molecular charge (e.g. 0):")
                else:
                    await query.edit_message_text("Psi4 — solvation model:", reply_markup=core.build_markup(PSI4_SOLVATION, "psisolv", 1))

            elif data.startswith("psisolv_"):
                solvation = data.split("_", 1)[1]
                ud["psi_solvation"] = solvation
                if solvation == "none":
                    ud["state"] = "psi_waiting_for_charge"
                    await query.edit_message_text("Psi4 — enter molecular charge (e.g. 0):")
                else:
                    await query.edit_message_text("Psi4 — select PCM solvent:", reply_markup=core.build_markup(PSI4_SOLVENTS, "psisolvnt", 2))

            elif data.startswith("psisolvnt_"):
                ud["psi_solvent"] = data.split("_", 1)[1]
                ud["state"] = "psi_waiting_for_charge"
                await query.edit_message_text("Psi4 — enter molecular charge (e.g. 0):")

            elif data.startswith("psitda_"):
                ud["psi_tda"] = data.endswith("yes")
                ud["state"] = "psi_waiting_for_cores"
                await query.edit_message_text("Psi4 — enter number of CPU threads (e.g. 4):")
        except Exception as exc:
            core.orca_logger.error("Psi4 callback error: %s", exc, exc_info=True)
            if query.message:
                await query.message.reply_text(f"❌ Psi4 wizard error: {html.escape(str(exc))}")

    async def handle_text_input(update, context):
        ud = context.user_data
        state = ud.get("state", "")
        if not state.startswith("psi_"):
            await original_text(update, context)
            return
        text = (update.message.text or "").strip()

        if state == "psi_waiting_for_charge":
            value = core._parse_int_in_range(text, -20, 20)
            if value is None:
                await update.message.reply_text("Enter an integer charge from -20 to 20.")
                return
            ud["charge"] = value
            ud["state"] = "psi_waiting_for_multiplicity"
            await update.message.reply_text("Psi4 — enter spin multiplicity (1=singlet, 2=doublet, 3=triplet, ...):")
            return

        if state == "psi_waiting_for_multiplicity":
            value = core._parse_int_in_range(text, 1, 20)
            if value is None:
                await update.message.reply_text("Enter a multiplicity between 1 and 20.")
                return
            ud["mult"] = value
            if ud.get("psi_calc_type") == "tddft":
                ud["state"] = "psi_waiting_for_nroots"
                await update.message.reply_text("Psi4 TD-SCF — number of excited states (e.g. 10):")
            else:
                ud["state"] = "psi_waiting_for_cores"
                await update.message.reply_text("Psi4 — number of CPU threads (e.g. 4):")
            return

        if state == "psi_waiting_for_nroots":
            value = core._parse_int_in_range(text, 1, 200)
            if value is None:
                await update.message.reply_text("Enter 1–200 excited states.")
                return
            ud["nroots"] = value
            await update.message.reply_text("Psi4 TD-SCF — use Tamm-Dancoff approximation (TDA)?", reply_markup=core.build_markup({"No (full TD-SCF/RPA)": "no", "Yes (TDA)": "yes"}, "psitda", 1))
            ud.pop("state", None)
            return

        if state == "psi_waiting_for_cores":
            value = core._parse_int_in_range(text, 1, 256)
            if value is None:
                await update.message.reply_text("Enter 1–256 CPU threads.")
                return
            ud["cores"] = value
            ud["state"] = "psi_waiting_for_memory"
            await update.message.reply_text("Psi4 — total memory in MB (not per-core), e.g. 4000:")
            return

        if state == "psi_waiting_for_memory":
            value = core._parse_int_in_range(text, 256, 512000)
            if value is None:
                await update.message.reply_text("Enter total memory from 256 to 512000 MB.")
                return
            ud["psi_memory_mb"] = value
            try:
                content = generate_psi4_input(ud)
            except Exception as exc:
                await update.message.reply_text(f"❌ Could not generate Psi4 input: {html.escape(str(exc))}")
                return
            filename = f"{ud.get('name', 'molecule')}_psi4.dat"
            bio = io.BytesIO(content.encode("utf-8"))
            bio.name = filename
            method = _psi4_target_method(ud)
            caption = (
                f"✅ Psi4 input generated: {filename}\n"
                f"Task: {ud.get('psi_calc_type')} | Method: {method} | Basis: {ud.get('psi_basis')}\n"
                "Run with: psi4 -i input.dat -o output.dat"
            )
            await update.message.reply_document(document=bio, caption=caption)
            ud.clear()
            return

    core.start_command = start_command
    core.handle_name = handle_name
    core.handle_file = handle_file
    core.callback_handler = callback_handler
    core.handle_text_input = handle_text_input
    core.generate_psi4_input = generate_psi4_input
    core.orca_logger.info("Dual ORCA 6.1 + Psi4 input wizard installed.")
