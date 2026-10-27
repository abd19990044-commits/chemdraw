"""Scientific output-geometry extraction and up-to-20-file batch inputs.

Additive extension for the existing Telegram webhook quantum-input generator.
Files and pending jobs are kept in the existing bounded, RAM-only user state.
No quantum chemistry engines or untrusted input scripts are executed.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
from dataclasses import dataclass
from typing import Any

from rdkit import Chem

from geometry_normalizer import normalize_geometry_xyz
from quantum_validation import bounded_int, checked_molecule, safe_stem
import gaussian_input as gaussian

MAX_BATCH_FILES = 20
MAX_OUTPUT_BYTES = 12 * 1024 * 1024
MAX_COORD_BYTES = 512 * 1024
COORD_SUFFIXES = frozenset({".xyz", ".mol", ".sdf"})
OUTPUT_SUFFIXES = frozenset({".log", ".out"})
SUPPORTED_SUFFIXES = COORD_SUFFIXES | OUTPUT_SUFFIXES

GAUSSIAN_ORIENTATION = re.compile(
    r"^\s*(?:Standard|Input|Z-Matrix)\s+orientation\s*:", re.I
)
ORCA_CARTESIAN = re.compile(
    r"^\s*CARTESIAN\s+COORDINATES\s*\(\s*ANGSTR(?:OEM|OM|OMS)?\s*\)", re.I
)
PSI4_GEOMETRY = re.compile(
    r"(?:Geometry\s*\(\s*in\s+(Angstroms?|Bohr)\s*\)|"
    r"Final optimized geometry\s*:)", re.I
)
BOHR_TO_ANGSTROM = 0.529177210903
TABLE = Chem.GetPeriodicTable()


@dataclass(frozen=True)
class ExtractedGeometry:
    xyz: str
    engine: str
    normal_termination: bool
    warning: str


def _numeric(token: str) -> float:
    return float(token.replace("D", "E").replace("d", "e"))


def _read_table(lines: list[str], start: int, engine: str, bohr: bool = False) -> str | None:
    rows: list[str] = []
    for line in lines[start : start + 1020]:
        parts = line.split()
        parsed = None
        if engine == "Gaussian":
            # Center Number, Atomic Number, Atomic Type, X, Y, Z
            if (len(parts) >= 6 and all(part.isdigit() for part in parts[:3])):
                atomic_no = int(parts[1])
                if 1 <= atomic_no <= 118:
                    try:
                        parsed = (TABLE.GetElementSymbol(atomic_no),
                                  [_numeric(x) for x in parts[3:6]])
                    except ValueError:
                        pass
        else:
            # ORCA: C x y z; Psi4: C x y z or 1 C x y z
            if len(parts) == 5 and parts[0].isdigit():
                parts = parts[1:]
            if len(parts) == 4 and re.fullmatch(r"[A-Z][a-z]?", parts[0]):
                if 1 <= TABLE.GetAtomicNumber(parts[0]) <= 118:
                    try:
                        parsed = (parts[0], [_numeric(x) for x in parts[1:]])
                    except ValueError:
                        pass
        if parsed:
            symbol, numbers = parsed
            if bohr:
                numbers = [n * BOHR_TO_ANGSTROM for n in numbers]
            rows.append(symbol + " " + " ".join(map(str, numbers)))
            if len(rows) > 1000:
                return None
        elif rows:
            break  # do not accidentally append geometry from the next table
    if not rows:
        return None
    try:
        return normalize_geometry_xyz("\n".join(rows))
    except ValueError:
        return None


def extract_final_geometry(text: str) -> ExtractedGeometry:
    """Return the last *printed* Cartesian geometry, with honest status metadata.

    Last-printed coordinates are not necessarily an optimized minimum or TS.
    A normal-termination marker is a separate, weaker property than convergence.
    """
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("Empty or binary output.")
    lines = text.splitlines()
    candidates: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if GAUSSIAN_ORIENTATION.match(line):
            coords = _read_table(lines, i + 1, "Gaussian")
            if coords:
                candidates.append((i, "Gaussian", coords))
        elif ORCA_CARTESIAN.match(line):
            coords = _read_table(lines, i + 1, "ORCA")
            if coords:
                candidates.append((i, "ORCA", coords))
        else:
            match = PSI4_GEOMETRY.search(line)
            if match:
                coords = _read_table(
                    lines, i + 1, "Psi4",
                    bohr=bool(match.group(1) and match.group(1).lower() == "bohr")
                )
                if coords:
                    candidates.append((i, "Psi4", coords))
    if not candidates:
        raise ValueError(
            "No supported Cartesian geometry found in this LOG/OUT. "
            "Supported formats: Gaussian orientation tables, ORCA Cartesian "
            "coordinates (Angstroem), Psi4 Geometry (in Angstrom/Bohr)."
        )
    index, engine, xyz = candidates[-1]
    remaining = "\n".join(lines[index + 1 :]).lower()
    markers = {
        "Gaussian": "normal termination of gaussian",
        "ORCA": "orca terminated normally",
        "Psi4": "psi4 exiting successfully",
    }
    normal = markers[engine] in remaining
    note = (
        "This is the last printed geometry, not a verified optimized minimum. "
        "Normal termination was not confirmed after this geometry."
        if not normal else
        "This is the last printed geometry. Normal termination alone does not "
        "prove geometry-optimization convergence or correct frequencies."
    )
    return ExtractedGeometry(xyz, engine, normal, note)


def read_structure(raw: bytes, suffix: str) -> tuple[str, str]:
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("Supported extensions: XYZ, MOL, SDF, LOG, OUT.")
    max_size = MAX_OUTPUT_BYTES if suffix in OUTPUT_SUFFIXES else MAX_COORD_BYTES
    if not raw or len(raw) > max_size:
        raise ValueError(f"Empty or oversized upload (limit {max_size // 1024} KiB).")
    if b"\x00" in raw:
        raise ValueError("Binary structures are not supported.")
    text = raw.decode("utf-8-sig", errors="replace")
    if suffix in OUTPUT_SUFFIXES:
        result = extract_final_geometry(text)
        return result.xyz, f"Source: {result.engine}. {result.warning}"
    if suffix == ".xyz":
        return normalize_geometry_xyz(text), ""
    # SDF first record; explicit/implicit H atoms must not be silently discarded.
    mol = Chem.MolFromMolBlock(
        text.split("$$$$", 1)[0].rstrip() + "\n",
        removeHs=False, sanitize=True,
    )
    if mol is None or not mol.GetNumConformers():
        raise ValueError("Invalid MOL/SDF with no atomic coordinates.")
    if not mol.GetConformer().Is3D():
        raise ValueError(
            "This MOL/SDF declares a 2D depiction, not a 3D calculation geometry."
        )
    explicit = mol.GetNumAtoms()
    mol = Chem.AddHs(mol, addCoords=True)
    conf = mol.GetConformer()
    xyz = "\n".join(
        f"{atom.GetSymbol()} {conf.GetAtomPosition(atom.GetIdx()).x} "
        f"{conf.GetAtomPosition(atom.GetIdx()).y} "
        f"{conf.GetAtomPosition(atom.GetIdx()).z}"
        for atom in mol.GetAtoms()
    )
    warning = (
        "Implicit H coordinates were constructed; optimize the starting geometry."
        if mol.GetNumAtoms() > explicit else ""
    )
    return normalize_geometry_xyz(xyz), warning


def prepare_batch_inputs(
    batch: list[dict[str, str]], settings: dict[str, Any], engine: str, core
) -> list[tuple[str, bytes]]:
    """Validate *all* individual molecules before sending the first file."""
    if not 1 <= len(batch) <= MAX_BATCH_FILES:
        raise ValueError(f"Provide 1 to {MAX_BATCH_FILES} structures.")
    generators = {
        "orca": (".inp", core.generate_orca_6_input),
        "psi4": (".dat", core.generate_psi4_input),
        "gaussian": (".gjf", gaussian.generate_gaussian_16_input),
    }
    if engine not in generators:
        raise ValueError("Unsupported quantum engine.")
    suffix, generate = generators[engine]
    generated: list[tuple[str, bytes]] = []
    errors: list[str] = []
    for index, item in enumerate(batch, 1):
        name = safe_stem(item["name"])
        data = dict(settings, name=name, coords=item["xyz"])
        try:
            checked_molecule(data)
            content = generate(data)
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty generated input.")
            generated.append(
                (f"{index:02d}_{name}{suffix}", content.encode("utf-8"))
            )
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{index:02d} {name}: {exc}")
    if errors:
        raise ValueError(
            "Batch cancelled before sending files because of invalid structure(s):\n"
            + "\n".join(errors)
            + "\nDifferent charges/multiplicities need separate batches."
        )
    return generated


def install_quantum_batch(core) -> None:
    """Wrap the existing handlers only after all other extensions are installed."""
    old_start = core.start_command
    old_file = core.handle_file
    old_text = core.handle_text_input

    async def deliver_individually(update, ud):
        files = prepare_batch_inputs(
            ud["_batch_structures"], ud, ud["engine"], core
        )
        ud["state"] = "batch_sending"
        start = int(ud.get("_batch_sent_count", 0))
        for i in range(start, len(files)):
            name, raw = files[i]
            document = io.BytesIO(raw)
            document.name = name
            try:
                await update.message.reply_document(
                    document=document,
                    caption=f"✅ Input {i + 1}/{len(files)} — {name}",
                )
            except Exception as exc:
                await update.message.reply_text(
                    f"⚠️ Sending stopped after {i} files. "
                    f"Use /retry_batch to continue from file {i + 1}. "
                    f"Error: {str(exc)[:180]}"
                )
                return
            ud["_batch_sent_count"] = i + 1
            if i < len(files) - 1:
                # Telegram's per-chat flood limits favor roughly one send/second.
                await asyncio.sleep(1.05)
        ud.clear()
        await update.message.reply_text(
            f"✅ All {len(files)} input files were sent separately (no ZIP)."
        )

    async def start_command(update, context):
        # Preserve the pre-existing single /start welcome response, on which
        # both webhook clients and the public regression suite rely.  The
        # batch instructions are part of quantum_input_extension.WELCOME.
        await old_start(update, context)

    async def handle_file(update, context):
        doc = update.message.document
        filename = doc.file_name or "geometry"
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in SUPPORTED_SUFFIXES:
            await old_file(update, context)
            return
        ud = context.user_data
        batch = ud.get("_batch_structures")
        if batch is None and suffix not in OUTPUT_SUFFIXES:
            await old_file(update, context)
            return
        if batch is not None:
            if ud.get("_batch_ready"):
                await update.message.reply_text(
                    "Configuration has started. Finish it or use /batch to restart."
                )
                return
            if len(batch) >= MAX_BATCH_FILES:
                await update.message.reply_text(
                    "Maximum 20 inputs. Send /done to configure your batch."
                )
                return
        limit = MAX_OUTPUT_BYTES if suffix in OUTPUT_SUFFIXES else MAX_COORD_BYTES
        declared_size = (
            getattr(update.message, "_raw", {}).get("document", {}).get("file_size", 0)
        )
        if declared_size and declared_size > limit:
            await update.message.reply_text(
                f"❌ {safe_stem(filename)} exceeds {limit // 1024} KiB."
            )
            return
        try:
            remote = await context.bot.get_file(doc.file_id)
            raw = await remote.download_as_bytes()
            coords, note = read_structure(raw, suffix)
        except Exception as exc:
            await update.message.reply_text(
                f"❌ {safe_stem(filename)}: {str(exc)[:500]}"
            )
            return
        stem = safe_stem(os.path.splitext(filename)[0])
        if batch is not None:
            batch.append({"name": stem, "xyz": coords, "note": note})
            await update.message.reply_text(
                f"✅ Loaded {len(batch)}/20: {stem}"
                + (f"\n⚠️ {note[:350]}" if note else "")
                + "\nSend another file or /done."
            )
            return
        ud.clear()
        ud.update(name=stem, coords=coords, state="waiting_for_engine")
        xyz_file = io.BytesIO(
            f"{len(coords.splitlines())}\n{stem}\n{coords}\n".encode("utf-8")
        )
        xyz_file.name = f"{stem}_last_geometry.xyz"
        await update.message.reply_document(
            document=xyz_file,
            caption="Last printed XYZ coordinates (Angstrom); not necessarily converged.",
        )
        await update.message.reply_text(
            f"✅ Extracted {len(coords.splitlines())} atoms from {stem}."
            + (f"\n⚠️ {note[:400]}" if note else "")
            + "\nSelect calculation engine:",
            reply_markup=core.build_markup({
                "ORCA 6.1": "orca",
                "Psi4": "psi4",
                "Gaussian 16 (Linux)": "gaussian",
            }, "engine", 2),
        )

    async def handle_text_input(update, context):
        ud = context.user_data
        text = (update.message.text or "").strip()
        if re.fullmatch(r"/batch(?:@\w+)?", text, re.I):
            ud.clear()
            ud["_batch_structures"] = []
            ud["_batch_ready"] = False
            await update.message.reply_text(
                "📂 Upload 1–20 files: XYZ, MOL, SDF, Gaussian LOG, or "
                "ORCA/Psi4 OUT. Send /done when all files are received. "
                "Every document is processed independently; no ZIP is used."
            )
            return
        if re.fullmatch(r"/batch_status(?:@\w+)?", text, re.I):
            batch = ud.get("_batch_structures")
            await update.message.reply_text(
                "No active batch: use /batch."
                if batch is None else f"Loaded {len(batch)}/20 files."
            )
            return
        if re.fullmatch(r"/done(?:@\w+)?", text, re.I):
            batch = ud.get("_batch_structures")
            if batch is None:
                await update.message.reply_text("Send /batch first.")
                return
            if not batch:
                await update.message.reply_text("Upload at least one document.")
                return
            if ud.get("_batch_ready"):
                await update.message.reply_text(
                    "Continue the active configuration or send /batch to restart."
                )
                return
            ud["_batch_ready"] = True
            ud.update(
                name=batch[0]["name"], coords=batch[0]["xyz"],
                state="waiting_for_engine",
            )
            await update.message.reply_text(
                f"✅ {len(batch)} structures ready. Choose ONE engine, method, "
                "basis, solvent and charge/multiplicity for all structures. "
                "Every molecule will be validated independently.",
                reply_markup=core.build_markup({
                    "ORCA 6.1": "orca",
                    "Psi4": "psi4",
                    "Gaussian 16 (Linux)": "gaussian",
                }, "engine", 2),
            )
            return
        if re.fullmatch(r"/retry_batch(?:@\w+)?", text, re.I):
            if ud.get("state") != "batch_sending":
                await update.message.reply_text(
                    "No interrupted delivery. Use /batch for a new batch."
                )
                return
            await deliver_individually(update, ud)
            return

        # Intercept only the *final* wizard text step: the existing UI/choices
        # and validators remain untouched for the three supported engines.
        finish = {
            ("orca", "waiting_for_ram"): ("ram", 100, 64000),
            ("psi4", "psi_waiting_for_memory"): ("psi_memory_mb", 256, 512000),
            ("gaussian", "g_disk"): ("gauss_disk_mb", 100, 20000),
        }.get((ud.get("engine"), ud.get("state")))
        if ud.get("_batch_ready") and ud.get("_batch_structures") and finish:
            key, lower, upper = finish
            try:
                ud[key] = bounded_int(text, key, lower, upper)
                # Validate every file before beginning a potentially long send.
                prepare_batch_inputs(ud["_batch_structures"], ud, ud["engine"], core)
            except (ValueError, KeyError, TypeError) as exc:
                await update.message.reply_text(
                    f"❌ Batch verification:\n{str(exc)[:3300]}\n"
                    "Correct the last parameter, or /batch to restart."
                )
                return
            ud["_batch_sent_count"] = 0
            await deliver_individually(update, ud)
            return
        if ud.get("state") == "batch_sending":
            await update.message.reply_text(
                "Files are being delivered. Use /retry_batch if delivery stopped."
            )
            return
        await old_text(update, context)

    core.start_command = start_command
    core.handle_file = handle_file
    core.handle_text_input = handle_text_input
    core.orca_logger.info(
        "Output-geometry import and individual 20-file batch delivery enabled"
    )
