"""Gaussian 16 wizard and expert input paths for the webhook quantum bot only."""

from __future__ import annotations

import asyncio
import contextvars
import io
import re
import weakref

import gaussian_input as g16
import psi4_extension as psi4_ui
from geometry_normalizer import normalize_geometry_xyz
from quantum_validation import (
    MAX_MANUAL_BYTES,
    bounded_int,
    checked_molecule,
    manual_input,
    safe_stem,
)


WELCOME = (
    "🧮 Quantum Chemistry Input Generator\n\n"
    "Create input files for ORCA 6.1 (.inp), Psi4 (.dat), and Gaussian 16 for Linux (.gjf).\n\n"
    "Start with #benzene, upload XYZ/MOL/SDF coordinates, or send /xyz to paste coordinates. "
    "Then select an engine and follow the buttons.\n\n"
    "For complete expert inputs, send /manual and paste the Gaussian or Psi4 file, "
    "or upload a GaussView .gjf/.com or Psi4 .dat file. The bot prepares files; it never runs calculations.\n"
    "Use /cancel to restart at any time. Gaussian guided jobs limit MaxDisk to 20000 MB."
)


def _engine_markup(core):
    return core.build_markup(
        {"ORCA 6.1": "orca", "Psi4": "psi4", "Gaussian 16 (Linux)": "gaussian"},
        "engine",
        2,
    )


def _manual_markup(core):
    return core.build_markup(
        {
            "Gaussian 16 (.gjf)": "quantum_manual_gaussian",
            "Psi4 (.dat)": "quantum_manual_psi4",
        },
        "select",
        1,
    )


async def _send_file(message, content: str, name: str, caption: str) -> None:
    document = io.BytesIO(content.encode("utf-8"))
    document.name = name
    await message.reply_document(document=document, caption=caption)


def _safe_orca_generator(core, original):
    def generate(data: dict) -> str:
        safe = dict(data)
        safe["coords"], safe["charge"], safe["mult"] = checked_molecule(safe)
        safe["cores"] = bounded_int(safe.get("cores", 4), "CPU cores", 1, 128)
        safe["ram"] = bounded_int(
            safe.get("ram", 6000), "Memory per core (MB)", 100, 64000
        )
        task = safe.get("calc_type", "sp")
        if task not in {"sp", "opt", "opt freq", "optts", "tddft", "custom"}:
            raise ValueError(
                "Unsupported ORCA task. Use the manual keyword line for expert jobs."
            )
        if task == "tddft":
            safe["nroots"] = bounded_int(
                safe.get("nroots", 10), "Excited states", 1, 200
            )
        if "custom_line" in safe:
            line = str(safe["custom_line"]).strip()
            if (
                not line.startswith("!")
                or "\n" in line
                or "\r" in line
                or len(line) > 500
            ):
                raise ValueError(
                    "ORCA custom keywords must be one line beginning with ! (up to 500 characters)."
                )
            safe["custom_line"] = line
        else:
            if task == "tddft" and safe.get("family") != "f_dft":
                raise ValueError(
                    "The guided ORCA TD-DFT task requires a DFT functional."
                )
            if task in {"opt", "opt freq", "optts"} and safe.get("family") == "f_ccsd":
                raise ValueError(
                    "The guided CCSD branch is for single points; use custom input for derivatives."
                )
            method = str(safe.get("theory", ""))
            choices = set(
                core.COMPOSITE_METHODS
                + core.DFT_FUNCTIONALS
                + core.MP2_VARIANTS
                + core.CCSD_VARIANTS
                + ["HF"]
            )
            if method not in choices:
                raise ValueError(
                    "Unsupported ORCA method. Select a preset or use custom keywords."
                )
            if safe.get("family") != "f_comp" and safe.get("basis") not in {
                b for choices in core.BASIS_MAP.values() for b in choices
            }:
                raise ValueError("Select a valid orbital basis for this ORCA method.")
            dispersion = str(safe.get("disp", "none")).lower()
            if dispersion not in {"none", "d3", "d3bj", "d4"} or (
                dispersion != "none"
                and (
                    safe.get("family") != "f_dft"
                    or method.lower().endswith(("-v", "-d", "-d4"))
                )
            ):
                raise ValueError(
                    "Do not add a second dispersion correction to this method."
                )
            if safe.get("ri_type", "none") not in {"none", "rijcosx", "rijk", "ri"}:
                raise ValueError("Invalid ORCA RI option.")
            if (
                safe.get("family") in {"f_mp2", "f_ccsd", "f_comp"}
                and safe.get("ri_type", "none") != "none"
            ):
                raise ValueError(
                    "The preset post-HF/composite method already selects its own acceleration."
                )
            if safe.get("solv_model", "none") not in {"none", "cpcm", "smd"}:
                raise ValueError("Invalid ORCA solvation model.")
            if (
                safe.get("family") in {"f_mp2", "f_ccsd"}
                and safe.get("solv_model") == "smd"
            ):
                raise ValueError(
                    "The guided ORCA post-HF branch does not offer SMD; use CPCM or expert input."
                )
            if (
                safe.get("solv_model", "none") != "none"
                and safe.get("solvent", "Water") not in core.SOLVENTS
            ):
                raise ValueError("Invalid ORCA solvent.")
        return original(safe)

    return generate


def install_quantum_input_extension(core) -> None:
    """Install on the first bot after the existing Psi4 overlay is active."""
    original_callback = core.callback_handler
    original_text = core.handle_text_input
    original_file = core.handle_file
    original_dispatch = core._dispatch_update
    original_orca = core.generate_orca_6_input
    core.generate_orca_6_input = _safe_orca_generator(core, original_orca)
    core.MP2_VARIANTS = ["MP2", "RI-MP2 AutoAux", "DLPNO-MP2 AutoAux"]
    core.CCSD_VARIANTS = [
        "CCSD",
        "CCSD(T)",
        "DLPNO-CCSD AutoAux",
        "DLPNO-CCSD(T) AutoAux",
    ]
    psi4_ui._engine_markup = _engine_markup

    # The legacy webhook bot stored one wizard per *chat*. In a group two users
    # could silently overwrite each other's charge/coordinates. Scope its state
    # to chat + sender and serialize updates for that identity.
    identity = contextvars.ContextVar("quantum_user_identity", default=None)
    locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

    def state_key(chat_id):
        current = identity.get()
        return f"quantum:{current[0]}:{current[1]}" if current else f"user:{chat_id}"

    async def load_user_data(chat_id):
        return core._state_cache.get(state_key(chat_id), {}).copy()

    async def save_user_data(chat_id, data):
        key = state_key(chat_id)
        if data:
            core._state_cache[key] = data.copy()
        else:
            core._state_cache.pop(key, None)

    async def clear_user_data(chat_id):
        core._state_cache.pop(state_key(chat_id), None)

    async def dispatch(bot, raw):
        message = (
            raw.get("message") or raw.get("callback_query", {}).get("message") or {}
        )
        sender = (raw.get("message") or raw.get("callback_query") or {}).get(
            "from"
        ) or {}
        chat = message.get("chat") or {}
        key = (chat.get("id"), sender.get("id", chat.get("id")))
        lock = locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            locks[key] = lock
        async with lock:
            token = identity.set(key)
            try:
                await original_dispatch(bot, raw)
            finally:
                identity.reset(token)

    core._load_user_data = load_user_data
    core._save_user_data = save_user_data
    core._clear_user_data = clear_user_data
    core._dispatch_update = dispatch

    # The webhook and its management routes must not become public endpoints
    # because a deployment omitted the shared secret.
    original_webhook = core.app.view_functions["telegram_webhook_route"]
    original_secret_check = core._check_secret

    def configured_secret_check(request):
        return bool(core.ORCA_WEBHOOK_SECRET) and original_secret_check(request)

    def secured_webhook():
        if not core.ORCA_WEBHOOK_SECRET:
            return "ORCA_WEBHOOK_SECRET must be configured.", 503
        return original_webhook()

    core._check_secret = configured_secret_check
    core.app.view_functions["telegram_webhook_route"] = secured_webhook

    async def start_command(update, context):
        context.user_data.clear()
        await update.message.reply_text(WELCOME)

    async def _manual_result(message, ud, text, engine, source_name=None):
        try:
            content = manual_input(text, engine)
        except ValueError as exc:
            await message.reply_text(
                f"❌ {exc} Correct the input and try again, or /cancel."
            )
            return
        stem = safe_stem(source_name or ud.get("name") or "manual_input")
        suffix = "gjf" if engine == "gaussian" else "dat"
        await _send_file(
            message,
            content,
            f"{stem}.{suffix}",
            f"✅ {engine.title()} manual input saved. Check the calculation before running it on Linux.",
        )
        ud.clear()

    async def handle_file(update, context):
        document = update.message.document
        filename = document.file_name or "input"
        ext = core.os.path.splitext(filename)[1].lower()
        manual_engine = {".gjf": "gaussian", ".com": "gaussian", ".dat": "psi4"}.get(
            ext
        )
        if manual_engine:
            if (
                update.message._raw.get("document", {}).get("file_size", 0)
                > MAX_MANUAL_BYTES
            ):
                await update.message.reply_text(
                    "❌ Manual input is larger than 128 kB."
                )
                return
            try:
                remote = await context.bot.get_file(document.file_id)
                raw = await remote.download_as_bytes()
                if len(raw) > MAX_MANUAL_BYTES:
                    raise ValueError("Manual input is larger than 128 kB.")
                decoded = raw.decode("utf-8-sig")
            except (UnicodeDecodeError, ValueError) as exc:
                await update.message.reply_text(f"❌ Cannot read text input: {exc}")
                return
            await _manual_result(
                update.message,
                context.user_data,
                decoded,
                manual_engine,
                core.os.path.splitext(filename)[0],
            )
            return
        if (
            ext in {".xyz", ".mol", ".sdf"}
            and update.message._raw.get("document", {}).get("file_size", 0)
            > MAX_MANUAL_BYTES
        ):
            await update.message.reply_text("❌ Coordinate file is larger than 128 kB.")
            return
        await original_file(update, context)

    async def callback_handler(update, context):
        query = update.callback_query
        data = query.data or ""
        ud = context.user_data
        if data == "cancel_op":
            ud.clear()
            await query.answer()
            await query.edit_message_text(
                "Cancelled. Send #benzene, /xyz, /manual, or a coordinate file to begin."
            )
            return
        if data.startswith("select_quantum_manual_"):
            await query.answer()
            engine = data.rsplit("_", 1)[1]
            ud["state"] = f"manual_{engine}_input"
            await query.edit_message_text(
                f"Send the complete {engine.title()} input as text or upload a .{'gjf/.com' if engine == 'gaussian' else 'dat'} file. "
                "Expert calculation settings are preserved; the bot does not run them. /cancel aborts."
            )
            return
        if data == "engine_gaussian":
            await query.answer()
            if not ud.get("coords"):
                await query.edit_message_text(
                    "Coordinates expired. Send #benzene or /xyz to start again."
                )
                return
            ud["engine"] = "gaussian"
            await query.edit_message_text(
                "Gaussian 16 for Linux — choose a workflow:",
                reply_markup=core.build_markup(
                    {
                        "Guided presets": "guided_gaussian",
                        "Manual full input": "manual_gaussian",
                    },
                    "quantum",
                    1,
                ),
            )
            return
        if data == "engine_psi4":
            await query.answer()
            if not ud.get("coords"):
                await query.edit_message_text(
                    "Coordinates expired. Send #benzene or /xyz to start again."
                )
                return
            ud["engine"] = "psi4"
            await query.edit_message_text(
                "Psi4 — choose a workflow:",
                reply_markup=core.build_markup(
                    {
                        "Guided presets": "guided_psi4",
                        "Manual full Psithon": "manual_psi4",
                    },
                    "quantum",
                    1,
                ),
            )
            return
        if data == "quantum_guided_psi4":
            await query.answer()
            ud.pop("state", None)
            await query.edit_message_text(
                "Psi4 — calculation task:", reply_markup=psi4_ui._psi_task_markup(core)
            )
            return
        if data == "quantum_manual_gaussian" or data == "quantum_manual_psi4":
            await query.answer()
            engine = data.rsplit("_", 1)[1]
            ud["state"] = f"manual_{engine}_input"
            await query.edit_message_text(
                f"Paste the complete {engine.title()} input, or upload .{'gjf/.com' if engine == 'gaussian' else 'dat'}. "
                "The bot preserves your calculation settings and does not run the file. /cancel aborts."
            )
            return
        if data == "quantum_guided_gaussian":
            await query.answer()
            if not ud.get("coords"):
                await query.edit_message_text(
                    "Coordinates expired. Send #benzene or /xyz to start again."
                )
                return
            ud["state"] = "g_task"
            await query.edit_message_text(
                "Gaussian — task:",
                reply_markup=core.build_markup(g16.TASKS, "g_task", 2),
            )
            return
        if data.startswith("g_"):
            await query.answer()
            if not ud.get("coords") or ud.get("engine") != "gaussian":
                await query.edit_message_text(
                    "Gaussian session expired. Send /start to begin again."
                )
                return
            try:
                await _gaussian_callback(core, query, ud, data)
            except ValueError as exc:
                await query.edit_message_text(f"❌ {exc} Send /start to begin again.")
            return
        if ud.get("state", "").startswith("manual_"):
            await query.answer()
            await query.edit_message_text(
                "Manual input is active. Send the complete text or file, or /cancel."
            )
            return
        if data.startswith("psi") and ud.get("engine") != "psi4":
            await query.answer()
            await query.edit_message_text(
                "That Psi4 button belongs to an old session. Send /start to begin again."
            )
            return
        if (
            data.startswith(
                (
                    "calc_",
                    "fam_",
                    "meth_",
                    "disp_",
                    "ri_",
                    "bfam_",
                    "basis_",
                    "solv_",
                    "solvent_",
                    "rel_",
                )
            )
            and ud.get("engine") != "orca"
        ):
            await query.answer()
            await query.edit_message_text(
                "That ORCA button belongs to an old session. Send /start to begin again."
            )
            return
        if data.startswith("fam_") and ud.get("engine") == "orca":
            family = data.split("_", 1)[1]
            if ud.get("calc_type") == "tddft" and family != "f_dft":
                await query.answer()
                await query.edit_message_text(
                    "ORCA TD-DFT needs a DFT functional in the guided wizard. Select DFT:",
                    reply_markup=core.build_markup({"DFT functionals": "f_dft"}, "fam"),
                )
                return
            if (
                ud.get("calc_type") in {"opt", "opt freq", "optts"}
                and family == "f_ccsd"
            ):
                await query.answer()
                await query.edit_message_text(
                    "CCSD derivatives are not offered by the guided wizard. Choose a different family:",
                    reply_markup=core.build_markup(
                        {
                            label: val
                            for label, val in core.THEORY_FAMILIES.items()
                            if val != "f_ccsd"
                        },
                        "fam",
                    ),
                )
                return
        if data == "solv_smd" and ud.get("engine") == "orca" and ud.get("family") in {
            "f_mp2", "f_ccsd"
        }:
            await query.answer()
            await query.edit_message_text(
                "ORCA post-HF SMD is not a guided preset. Choose gas phase or CPCM:",
                reply_markup=core.build_markup(
                    {"Gas phase": "none", "CPCM": "cpcm"}, "solv"
                ),
            )
            return
        if (
            data.startswith("meth_")
            and ud.get("engine") == "orca"
            and ud.get("family") in {"f_mp2", "f_ccsd"}
        ):
            await query.answer()
            method = data.split("_", 1)[1]
            if method not in (
                core.MP2_VARIANTS if ud["family"] == "f_mp2" else core.CCSD_VARIANTS
            ):
                await query.edit_message_text("Invalid method. Send /start to restart.")
                return
            ud.update(theory=method, disp="none", ri_type="none")
            await query.edit_message_text(
                "ORCA — basis family:",
                reply_markup=core.build_markup(core.BASIS_FAMILIES, "bfam"),
            )
            return
        if data == "fam_f_hf" and ud.get("engine") == "orca":
            await query.answer()
            ud.update(family="f_hf", theory="HF", disp="none", ri_type="none")
            await query.edit_message_text(
                "ORCA HF — basis family:",
                reply_markup=core.build_markup(core.BASIS_FAMILIES, "bfam"),
            )
            return
        if (
            data.startswith("meth_")
            and ud.get("engine") == "orca"
            and ud.get("family") == "f_dft"
        ):
            method = data.split("_", 1)[1]
            if method.lower().endswith(("-v", "-d4")):
                await query.answer()
                ud.update(theory=method, disp="none")
                await query.edit_message_text(
                    "ORCA — acceleration:",
                    reply_markup=core.build_markup(core.RI_OPTIONS, "ri"),
                )
                return
        await original_callback(update, context)

    async def handle_text_input(update, context):
        text = (update.message.text or "").strip()
        ud = context.user_data
        if re.fullmatch(r"/(?:help|start)(?:@\w+)?", text, re.I):
            await start_command(update, context)
            return
        if re.fullmatch(r"/cancel(?:@\w+)?", text, re.I):
            ud.clear()
            await update.message.reply_text(
                "Cancelled. Send #benzene, /xyz, or /manual to begin."
            )
            return
        if re.fullmatch(r"/manual(?:@\w+)?", text, re.I):
            ud.clear()
            await update.message.reply_text(
                "Choose the engine for a complete manual input:",
                reply_markup=_manual_markup(core),
            )
            return
        if re.fullmatch(r"/xyz(?:@\w+)?", text, re.I):
            ud.clear()
            ud["state"] = "waiting_for_xyz"
            await update.message.reply_text(
                "Paste Cartesian XYZ coordinates (Element x y z), with or without an XYZ atom-count header. /cancel aborts."
            )
            return
        state = ud.get("state", "")
        if state == "waiting_for_xyz":
            try:
                coords = normalize_geometry_xyz(text)
            except ValueError as exc:
                await update.message.reply_text(
                    f"❌ {exc} Please correct the coordinates or /cancel."
                )
                return
            ud.update(coords=coords, name="manual_geometry", state="waiting_for_engine")
            await update.message.reply_text(
                "Choose quantum-chemistry engine:", reply_markup=_engine_markup(core)
            )
            return
        if state in {"manual_gaussian_input", "manual_psi4_input"}:
            await _manual_result(
                update.message, ud, update.message.text or "", state.split("_")[1]
            )
            return
        if state.startswith("g_"):
            await _gaussian_text(update.message, ud, text)
            return
        try:
            await original_text(update, context)
        except ValueError as exc:
            # The legacy ORCA wizard otherwise logs and drops generator errors.
            await update.message.reply_text(
                f"❌ {exc} Correct the last value or send /start to restart."
            )

    core.start_command = start_command
    core.handle_file = handle_file
    core.callback_handler = callback_handler
    core.handle_text_input = handle_text_input
    core.orca_logger.info(
        "Gaussian 16, manual input, and per-user quantum wizard installed."
    )


async def _gaussian_callback(core, query, ud, data):
    parts = data.split("_", 2)
    if len(parts) != 3:
        raise ValueError("Invalid Gaussian option.")
    _, field, value = parts
    if ud.get("state") != f"g_{field}":
        raise ValueError("This Gaussian button is no longer active.")
    if field == "task":
        choices = g16.TASKS
        ud["gauss_task"] = value if value in choices.values() else _invalid()
        ud["state"] = "g_method"
        methods = {
            label: method
            for label, method in g16.METHODS.items()
            if value in g16.allowed_tasks(method).values()
        }
        await query.edit_message_text(
            "Gaussian — method:", reply_markup=core.build_markup(methods, "g_method", 2)
        )
    elif field == "method":
        if (
            value not in g16.METHODS.values()
            or ud.get("gauss_task") not in g16.allowed_tasks(value).values()
        ):
            _invalid()
        ud["gauss_method"] = value
        ud["state"] = "g_basis"
        await query.edit_message_text(
            "Gaussian — orbital basis:",
            reply_markup=core.build_markup(g16.BASIS, "g_basis", 2),
        )
    elif field == "basis":
        if value not in g16.BASIS.values():
            _invalid()
        ud["gauss_basis"] = value
        if ud["gauss_method"] in {"B3LYP", "PBE1PBE"}:
            ud["state"] = "g_disp"
            await query.edit_message_text(
                "Gaussian — empirical dispersion:",
                reply_markup=core.build_markup(g16.DISPERSION, "g_disp"),
            )
        else:
            ud["gauss_dispersion"] = "none"
            await _ask_solvation(core, query, ud)
    elif field == "disp":
        if value not in g16.DISPERSION.values():
            _invalid()
        ud["gauss_dispersion"] = value
        await _ask_solvation(core, query, ud)
    elif field == "solv":
        if value not in g16.SOLVATION.values() or (
            ud["gauss_method"] in {"MP2", "CCSD(T)"} and value != "none"
        ):
            _invalid()
        ud["gauss_solvation"] = value
        if value == "none":
            await _ask_accuracy(core, query, ud)
        else:
            ud["state"] = "g_solvent"
            await query.edit_message_text(
                "Gaussian — solvent:",
                reply_markup=core.build_markup(g16.SOLVENTS, "g_solvent", 2),
            )
    elif field == "solvent":
        if value not in g16.SOLVENTS.values():
            _invalid()
        ud["gauss_solvent"] = value
        await _ask_accuracy(core, query, ud)
    elif field == "accuracy":
        if value not in g16.ACCURACY.values():
            _invalid()
        ud["gauss_accuracy"] = value
        ud["state"] = "g_charge"
        await query.edit_message_text("Gaussian — enter molecular charge (e.g. 0):")


def _invalid():
    raise ValueError("Unsupported selection.")


async def _ask_solvation(core, query, ud):
    ud["state"] = "g_solv"
    options = (
        {"Gas phase": "none"}
        if ud["gauss_method"] in {"MP2", "CCSD(T)"}
        else g16.SOLVATION
    )
    await query.edit_message_text(
        "Gaussian — solvation model:", reply_markup=core.build_markup(options, "g_solv")
    )


async def _ask_accuracy(core, query, ud):
    ud["state"] = "g_accuracy"
    await query.edit_message_text(
        "Gaussian — accuracy preset:",
        reply_markup=core.build_markup(g16.ACCURACY, "g_accuracy"),
    )


async def _gaussian_text(message, ud, text):
    state = ud["state"]
    specs = {
        "g_charge": (
            "charge",
            -20,
            20,
            "g_mult",
            "Gaussian — multiplicity (1 singlet, 2 doublet):",
        ),
        "g_mult": (
            "mult",
            1,
            21,
            "g_nroots" if ud.get("gauss_task") == "td" else "g_cores",
            "Gaussian — TD excited states (1–200):"
            if ud.get("gauss_task") == "td"
            else "Gaussian — CPU cores (1–128):",
        ),
        "g_nroots": ("nroots", 1, 200, "g_cores", "Gaussian — CPU cores (1–128):"),
        "g_cores": (
            "cores",
            1,
            128,
            "g_memory",
            "Gaussian — total memory in MB (e.g. 4000):",
        ),
        "g_memory": (
            "gauss_memory_mb",
            256,
            512000,
            "g_disk",
            "Gaussian — MaxDisk in MB (100–20000):",
        ),
        "g_disk": ("gauss_disk_mb", 100, 20000, "done", ""),
    }
    if state not in specs:
        await message.reply_text(
            "Choose one of the current Gaussian buttons, or send /cancel."
        )
        return
    key, lower, upper, next_state, prompt = specs[state]
    try:
        ud[key] = bounded_int(text, key, lower, upper)
        if state == "g_mult":
            checked_molecule(ud)
        if next_state == "done":
            content = g16.generate_gaussian_16_input(ud)
            stem = safe_stem(ud.get("name"))
            await _send_file(
                message,
                content,
                f"{stem}_g16.gjf",
                f"✅ Gaussian 16 input generated: {ud['gauss_method']}/{ud['gauss_basis']} | {ud['gauss_task']}. Run on Linux with: g16 < input.gjf > output.log",
            )
            ud.clear()
            return
    except ValueError as exc:
        await message.reply_text(f"❌ {exc} Correct this value, or send /cancel.")
        return
    ud["state"] = next_state
    await message.reply_text(prompt)
