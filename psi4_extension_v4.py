from __future__ import annotations

"""Psi4 v4: geometry-safe wizard plus validated dispersion-correction selection.

Dispersion is exposed only through Psi4 method aliases that are known to be
supported by the driver. Functionals with built-in nonlocal dispersion (e.g.
*-V) and composite methods are not offered an extra correction to avoid double
counting. D4 aliases require the optional dftd4 Python module at runtime.

Important implementation detail: this module never replaces v3's generator
with itself. The previous implementation did that and created an infinite
v4 -> v3(symbol now v4) -> v4 recursion. Instead v4 performs dispersion
resolution and geometry normalization once, then calls v3's captured stable v2
Psithon generator directly.
"""

from typing import Any

import psi4_extension as v1
import psi4_extension_v3 as v3
from geometry_normalizer import normalized_data_copy
from quantum_validation import checked_molecule, bounded_int, one_line, safe_stem


PSI4_DISPERSION_ALIASES: dict[str, dict[str, str]] = {
    "b3lyp": {
        "d3bj": "b3lyp-d3bj2b",
        "d3zero": "b3lyp-d3zero2b",
        "d4": "b3lyp-d4",
    },
    "pbe0": {
        "d3bj": "pbe0-d3bj2b",
        "d3zero": "pbe0-d3zero2b",
        "d4": "pbe0-d4",
    },
    "pbe": {
        "d3bj": "pbe-d3bj2b",
        "d3zero": "pbe-d3zero2b",
    },
    "bp86": {
        "d3bj": "bp86-d3bj2b",
        "d3zero": "bp86-d3zero2b",
    },
    "blyp": {
        "d3bj": "blyp-d3bj2b",
        "d3zero": "blyp-d3zero2b",
    },
    "cam-b3lyp": {
        "d3bj": "cam-b3lyp-d3bj2b",
    },
}

_BUILTIN_DISPERSION_MARKERS = (
    "-d3", "-d4", "-v", "b973c", "wb97x3c", "pbeh3c",
)


def dispersion_options_for_method(method: str) -> dict[str, str]:
    m = (method or "").strip().lower()
    if any(marker in m for marker in _BUILTIN_DISPERSION_MARKERS):
        return {"Built in / already included": "none"}
    aliases = PSI4_DISPERSION_ALIASES.get(m, {})
    options: dict[str, str] = {"None": "none"}
    if "d3bj" in aliases:
        options["Grimme D3(BJ)"] = "d3bj"
    if "d3zero" in aliases:
        options["Grimme D3(0)"] = "d3zero"
    if "d4" in aliases:
        options["Grimme D4 (requires dftd4 module)"] = "d4"
    return options


def apply_dispersion_alias(method: str, correction: str) -> str:
    m = (method or "").strip().lower()
    corr = (correction or "none").strip().lower()
    if corr == "none":
        return m
    if any(marker in m for marker in _BUILTIN_DISPERSION_MARKERS):
        raise ValueError(
            f"Method '{method}' already contains a dispersion/nonlocal correction; "
            "an additional empirical correction would risk double counting."
        )
    try:
        return PSI4_DISPERSION_ALIASES[m][corr]
    except KeyError as exc:
        raise ValueError(
            f"Psi4 dispersion option '{correction}' is not enabled for functional '{method}' "
            "because this wizard only exposes documented method aliases."
        ) from exc


def generate_psi4_input_v4(data: dict[str, Any]) -> str:
    """Resolve dispersion, normalize geometry, then build Psithon exactly once."""
    safe = dict(data)
    if safe.get("psi_family") == "dft":
        original = str(safe.get("psi_base_method") or safe.get("psi_method") or "b3lyp")
        correction = str(safe.get("psi_dispersion", "none"))
        safe["psi_method"] = apply_dispersion_alias(original, correction)

    # Do not call v3.generate_psi4_input_v3 here: installers intentionally
    # replace module-level generator symbols. Use v3's immutable captured base
    # generator after applying the shared strict geometry normalizer once.
    safe = normalized_data_copy(safe)
    coords, charge, mult = checked_molecule(safe)
    safe.update(coords=coords, charge=charge, mult=mult, name=safe_stem(safe.get("name")))
    safe["psi_basis"] = one_line(safe.get("psi_basis", "def2-tzvp"), "Psi4 basis", r"[A-Za-z0-9+*().,_-]+")
    safe["psi_method"] = one_line(safe.get("psi_method", "b3lyp"), "Psi4 method", r"[A-Za-z0-9+*().,_-]+")
    safe["cores"] = bounded_int(safe.get("cores", 4), "CPU cores", 1, 256)
    safe["psi_memory_mb"] = bounded_int(safe.get("psi_memory_mb", 4000), "Total memory (MB)", 256, 512000)
    if safe.get("psi_solvation", "none") == "pcm" and safe.get("psi_solvent", "Water") not in v1.PSI4_SOLVENTS.values():
        raise ValueError("Unsupported Psi4 PCM solvent. Use manual input for custom solvents.")
    return v3._ORIGINAL_GENERATOR(safe)


def install_psi4_extension_v4(core) -> None:
    # First install the complete v3/v2 wizard normally. This sets up all UI and
    # geometry-safe handlers without altering v3's generator symbol.
    v3.install_psi4_extension_v3(core)

    # v1's Telegram text handler resolves generate_psi4_input from its module
    # globals at call time, so redirect that single final generation point to
    # v4. This avoids recursive cross-module monkey-patching.
    v1.generate_psi4_input = generate_psi4_input_v4
    core.generate_psi4_input = generate_psi4_input_v4

    installed_callback = core.callback_handler

    async def callback_handler(update, context):
        query = update.callback_query
        data = query.data or ""
        ud = context.user_data

        if data.startswith("psimeth_") and ud.get("psi_family") == "dft":
            await query.answer()
            method = data.split("_", 1)[1].strip().lower()
            ud["psi_base_method"] = method
            ud["psi_method"] = method
            options = dispersion_options_for_method(method)
            if len(options) == 1:
                ud["psi_dispersion"] = "none"
                await query.edit_message_text(
                    "Psi4 — this functional already includes its dispersion/nonlocal model. "
                    "Select basis family:",
                    reply_markup=core.build_markup(v1.PSI4_BASIS_FAMILIES, "psibfam", 2),
                )
            else:
                await query.edit_message_text(
                    "Psi4 — dispersion correction (only documented combinations are shown):",
                    reply_markup=core.build_markup(options, "psidisp", 1),
                )
            return

        if data.startswith("psidisp_"):
            await query.answer()
            correction = data.split("_", 1)[1].strip().lower()
            base_method = str(ud.get("psi_base_method") or ud.get("psi_method") or "")
            try:
                resolved = apply_dispersion_alias(base_method, correction)
            except ValueError as exc:
                await query.edit_message_text(f"❌ {exc}")
                return
            ud["psi_dispersion"] = correction
            # Store the resolved alias for display; generate_psi4_input_v4 still
            # resolves from psi_base_method so repeated processing is idempotent.
            ud["psi_method"] = resolved
            note = ""
            if correction == "d4":
                note = "\n⚠️ D4 requires the optional Python dftd4 module in the Psi4 environment."
            await query.edit_message_text(
                f"Psi4 method: {resolved}{note}\n\nSelect basis family:",
                reply_markup=core.build_markup(v1.PSI4_BASIS_FAMILIES, "psibfam", 2),
            )
            return

        await installed_callback(update, context)

    core.callback_handler = callback_handler
    core.orca_logger.info("Psi4 v4 validated dispersion selector installed (non-recursive generator).")
