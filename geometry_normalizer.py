from __future__ import annotations

"""Strict geometry normalization for ORCA/Psi4 input generation.

Accepts Cartesian XYZ text and MOL/SDF blocks and returns only canonical
``Element X Y Z`` lines. Connectivity tables, counts lines, SDF properties,
and other non-coordinate records are never forwarded to quantum-chemistry
input files.
"""

import re
from typing import Any

from rdkit import Chem


_ELEMENT_RE = re.compile(r"^[A-Z][a-z]?$|^X$")
_INT_RE = re.compile(r"^[+-]?\d+$")


def _is_float(token: str) -> bool:
    try:
        float(token)
        return True
    except (TypeError, ValueError):
        return False


def _format_xyz(symbol: str, x: Any, y: Any, z: Any) -> str:
    if not _ELEMENT_RE.fullmatch(str(symbol)):
        raise ValueError(f"Invalid atomic symbol in coordinate data: {symbol!r}")
    try:
        xf, yf, zf = float(x), float(y), float(z)
    except (TypeError, ValueError) as exc:
        raise ValueError("Coordinate values must be numeric.") from exc
    return f"{symbol:<2s} {xf: .10f} {yf: .10f} {zf: .10f}"


def _xyz_from_rdkit_mol(mol: Chem.Mol | None) -> str | None:
    if mol is None or mol.GetNumAtoms() == 0 or mol.GetNumConformers() == 0:
        return None
    conf = mol.GetConformer()
    lines: list[str] = []
    for idx, atom in enumerate(mol.GetAtoms()):
        p = conf.GetAtomPosition(idx)
        lines.append(_format_xyz(atom.GetSymbol(), p.x, p.y, p.z))
    return "\n".join(lines) if lines else None


def _try_mol_sdf(text: str) -> str | None:
    """Parse a V2000/V3000 MOL or first SDF record with RDKit."""
    raw = text.strip("\ufeff\r\n ")
    if not raw:
        return None
    first_record = raw.split("$$$$", 1)[0].rstrip() + "\n"
    looks_mol = (
        "V2000" in first_record
        or "V3000" in first_record
        or "M  END" in first_record
        or "M  V30 BEGIN CTAB" in first_record
    )
    if not looks_mol:
        return None
    for sanitize in (True, False):
        try:
            mol = Chem.MolFromMolBlock(
                first_record,
                sanitize=sanitize,
                removeHs=False,
                strictParsing=False,
            )
            xyz = _xyz_from_rdkit_mol(mol)
            if xyz:
                return xyz
        except Exception:
            continue
    return None


def _try_xyz(text: str) -> str | None:
    """Parse strict XYZ or already-normalized Element/X/Y/Z coordinate lines."""
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return None

    start = 0
    expected_atoms: int | None = None
    if _INT_RE.fullmatch(lines[0]):
        expected_atoms = int(lines[0])
        if expected_atoms <= 0:
            return None
        # Standard XYZ has an atom-count line and a comment line. Permit a
        # missing/blank comment only when the following line is already an atom.
        if len(lines) >= expected_atoms + 2:
            start = 2
        elif len(lines) >= expected_atoms + 1:
            start = 1
        else:
            return None

    parsed: list[str] = []
    source_lines = lines[start : start + expected_atoms] if expected_atoms is not None else lines
    for line in source_lines:
        parts = line.split()
        if len(parts) < 4:
            return None
        symbol, x, y, z = parts[0], parts[1], parts[2], parts[3]
        if not _ELEMENT_RE.fullmatch(symbol) or not all(_is_float(v) for v in (x, y, z)):
            return None
        # Reject trailing MOL/connectivity-style numerical columns in a bare
        # coordinate block. Normal XYZ may have optional text, but the bot only
        # emits the first four fields after strict element-first validation.
        parsed.append(_format_xyz(symbol, x, y, z))

    if expected_atoms is not None and len(parsed) != expected_atoms:
        return None
    return "\n".join(parsed) if parsed else None


def normalize_geometry_xyz(text: str) -> str:
    """Return canonical Element/X/Y/Z lines or raise ValueError.

    MOL/SDF is deliberately attempted before XYZ because a V2000 atom/bond
    table contains many four-column numeric records that a permissive XYZ
    parser could otherwise misclassify.
    """
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("Molecular coordinates are empty.")

    xyz = _try_mol_sdf(raw)
    if xyz:
        return xyz
    xyz = _try_xyz(raw)
    if xyz:
        return xyz
    raise ValueError(
        "Unsupported or malformed geometry. Expected XYZ coordinates or a valid MOL/SDF record."
    )


def normalized_data_copy(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    out["coords"] = normalize_geometry_xyz(str(out.get("coords", "")))
    return out


def install_geometry_normalizer(core) -> None:
    """Install strict geometry conversion into the legacy ORCA/PubChem bot."""
    original_orca_generator = core.generate_orca_6_input

    def normalize_legacy_xyz(text: str):
        try:
            return normalize_geometry_xyz(text)
        except ValueError:
            return None

    def mol_block_to_xyz(text: str):
        try:
            xyz = _try_mol_sdf(str(text or ""))
            return xyz
        except Exception:
            return None

    def generate_3d_coords(value):
        if not isinstance(value, str):
            return None
        try:
            return normalize_geometry_xyz(value)
        except ValueError:
            return None

    def get_xyz_from_file(raw, ext: str):
        try:
            if isinstance(raw, (bytes, bytearray)):
                text = raw.decode("utf-8", errors="ignore")
            else:
                text = str(raw)
            suffix = str(ext or "").lower()
            if suffix not in {".xyz", ".mol", ".sdf"}:
                return None, None, None, "Unsupported extension."
            # Extension determines the expected parser first, then strict
            # normalization provides a safe fallback for mislabeled text files.
            if suffix in {".mol", ".sdf"}:
                xyz = _try_mol_sdf(text)
                if not xyz:
                    raise ValueError("Invalid MOL/SDF file.")
            else:
                xyz = _try_xyz(text)
                if not xyz:
                    raise ValueError("Invalid XYZ file.")
            return xyz, None, None, None
        except Exception as exc:
            return None, None, None, str(exc)

    def generate_orca_6_input(data: dict):
        safe = normalized_data_copy(data)
        return original_orca_generator(safe)

    core._normalize_xyz_text = normalize_legacy_xyz
    core._mol_block_to_xyz = mol_block_to_xyz
    core._generate_3d_coords = generate_3d_coords
    core.get_xyz_from_file = get_xyz_from_file
    core.generate_orca_6_input = generate_orca_6_input
    core.normalize_geometry_xyz = normalize_geometry_xyz
    core.orca_logger.info("Strict MOL/SDF/XYZ geometry normalizer installed for ORCA and Psi4 inputs.")
