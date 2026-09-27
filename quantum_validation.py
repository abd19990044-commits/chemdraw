"""Shared checks for the quantum-input bot. Nothing in this module runs jobs."""

from __future__ import annotations

import re

from rdkit import Chem

from geometry_normalizer import normalize_geometry_xyz

MAX_MANUAL_BYTES = 128_000
MAX_GAUSSIAN_DISK_MB = 20_000


def bounded_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[+-]?\d+", str(value).strip()):
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}.")
    result = int(str(value).strip())
    if not minimum <= result <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}.")
    return result


def checked_molecule(data: dict) -> tuple[str, int, int]:
    """Canonical coordinates and a necessary electron/spin parity check.

    This does not prove that the requested electronic state is chemically sound.
    """
    coords = normalize_geometry_xyz(str(data.get("coords", "")))
    charge = bounded_int(data.get("charge", 0), "Charge", -20, 20)
    mult = bounded_int(data.get("mult", 1), "Multiplicity", 1, 21)
    table = Chem.GetPeriodicTable()
    electrons = (
        sum(table.GetAtomicNumber(line.split()[0]) for line in coords.splitlines())
        - charge
    )
    if electrons < 1 or mult > electrons + 1 or (electrons - mult + 1) % 2:
        raise ValueError(
            f"Charge {charge} and multiplicity {mult} are inconsistent with "
            f"{electrons} electrons. Check the geometry and spin state."
        )
    return coords, charge, mult


def one_line(
    value: object, label: str, allowed: str = r"[A-Za-z0-9_+.,()=/\-]+"
) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160 or not re.fullmatch(allowed, text, re.ASCII):
        raise ValueError(
            f"Invalid {label}; use a single supported keyword without line breaks."
        )
    return text


def safe_stem(value: object) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "molecule")).strip("_")[:48]
    return stem or "molecule"


def manual_input(text: str, engine: str) -> str:
    """Preserve an expert input verbatim apart from newline encoding.

    The server never executes these files; full semantic validation requires
    the licensed Gaussian executable or the user's Psi4 installation.
    """
    if engine not in {"gaussian", "psi4"}:
        raise ValueError("Choose Gaussian 16 or Psi4 for manual input.")
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("The input file is empty or contains binary data.")
    content = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(content.encode("utf-8")) > MAX_MANUAL_BYTES:
        raise ValueError("Manual input is larger than 128 kB.")
    if engine == "gaussian":
        if not re.search(r"(?m)^\s*#(?:[pPnNtT])?\s+\S", content):
            raise ValueError("Gaussian input needs a route line starting with #.")
        # The manual route remains untouched. Check each explicit MaxDisk value,
        # including Link1 jobs; do not pretend that MaxDisk bounds every file.
        for match in re.finditer(r"\bMaxDisk\s*=\s*([^\s,]+)", content, re.I):
            parsed = re.fullmatch(r"(\d+)(KB|MB|GB|TB|KW|MW|GW|TW)?", match[1], re.I)
            if not parsed:
                raise ValueError(
                    "Gaussian MaxDisk must be a positive integer with an optional unit (e.g. 20000MB)."
                )
            amount, unit = int(parsed[1]), (parsed[2] or "W").upper()
            factors = {
                "KB": 1 / 1024,
                "MB": 1,
                "GB": 1024,
                "TB": 1024**2,
                "W": 8 / 1024**2,
                "KW": 8 / 1024,
                "MW": 8,
                "GW": 8 * 1024,
                "TW": 8 * 1024**2,
            }
            if amount <= 0 or amount * factors[unit] > MAX_GAUSSIAN_DISK_MB:
                raise ValueError(
                    "Gaussian MaxDisk must be positive and at most 20000 MB."
                )
    return content if content.endswith("\n") else content + "\n"
