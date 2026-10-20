"""Browsable Gaussian 16 functional/method and orbital-basis catalogue.

Each entry is an explicit Gaussian keyword, never a transformed display label.
The list is intentionally broad but not exhaustive: external/mixed Gen,
GenECP, ONIOM and custom basis blocks need complete expert input sections.
"""
from __future__ import annotations

import re

# Method families contain display label -> Gaussian 16 route keyword.
METHOD_FAMILIES: dict[str, dict[str, str]] = {
    "HF and post-HF": {
        "Hartree-Fock": "HF", "MP2": "MP2", "MP3 (SP)": "MP3",
        "CCSD (SP)": "CCSD", "CCSD(T) (SP)": "CCSD(T)",
        "QCISD (SP)": "QCISD", "QCISD(T) (SP)": "QCISD(T)",
    },
    "Pure GGA DFT": {
        "BLYP": "BLYP", "BP86": "BP86", "PBE (PBEPBE)": "PBEPBE",
        "BPW91": "BPW91", "PW91PW91": "PW91PW91", "OLYP": "OLYP",
        "B97-D (built-in dispersion)": "B97D",
    },
    "Hybrid GGA DFT": {
        "B3LYP": "B3LYP", "PBE0 (PBE1PBE)": "PBE1PBE",
        "B3P86": "B3P86", "B3PW91": "B3PW91",
        "BHandHLYP": "BHandHLYP", "O3LYP": "O3LYP", "X3LYP": "X3LYP",
        "B1LYP": "B1LYP",
    },
    "Meta-GGA DFT": {
        "TPSS": "TPSSTPSS", "M06-L": "M06L", "M11-L": "M11L",
        "MN15-L": "MN15L", "MN12-L": "MN12L",
    },
    "Meta-hybrid DFT": {
        "TPSSh": "TPSSh", "M05": "M05", "M05-2X": "M052X",
        "M06": "M06", "M06-2X": "M062X", "M06-HF": "M06HF",
        "M08-HX": "M08HX", "BMK": "BMK", "PW6B95": "PW6B95",
        "M11": "M11", "MN15": "MN15",
    },
    "Range-separated DFT": {
        "CAM-B3LYP": "CAM-B3LYP",
        "wB97X-D (built-in dispersion)": "wB97XD",
        "HSEh1PBE": "HSEh1PBE",
    },
    "Dispersion-built-in DFT": {
        "APFD": "APFD", "B97-D": "B97D",
        "wB97X-D": "wB97XD", "B97-D3": "B97D3",
        "PW6B95-D3": "PW6B95D3",
    },
    "Double-hybrid (expert SP)": {
        "B2PLYP": "B2PLYP", "mPW2PLYP": "mPW2PLYP",
        "PBE0-DH": "PBE0DH", "PBE-QIDH": "PBEQIDH",
    },
}
BASIS_FAMILIES: dict[str, dict[str, str]] = {
    "Minimal and split-valence": {
        "STO-3G": "STO-3G", "3-21G": "3-21G",
        "6-31G": "6-31G", "6-311G": "6-311G",
    },
    "Pople polarized": {
        "6-31G(d)": "6-31G(d)", "6-31G(d,p)": "6-31G(d,p)",
        "6-31G(2d,p)": "6-31G(2d,p)", "6-311G(d)": "6-311G(d)",
        "6-311G(d,p)": "6-311G(d,p)",
        "6-311G(2d,p)": "6-311G(2d,p)",
        "6-311G(2d,2p)": "6-311G(2d,2p)",
    },
    "Pople diffuse": {
        "6-31+G(d)": "6-31+G(d)", "6-31+G(d,p)": "6-31+G(d,p)",
        "6-31++G(d,p)": "6-31++G(d,p)",
        "6-311+G(d,p)": "6-311+G(d,p)",
        "6-311++G(d,p)": "6-311++G(d,p)",
        "6-311++G(2d,p)": "6-311++G(2d,p)",
    },
    "Ahlrichs def2": {
        "def2-SVP": "Def2SVP", "def2-SVPD": "Def2SVPD",
        "def2-TZVP": "Def2TZVP", "def2-TZVPP": "Def2TZVPP",
        "def2-TZVPD": "Def2TZVPD", "def2-QZVP": "Def2QZVP",
        "def2-QZVPP": "Def2QZVPP",
    },
    "Dunning cc-pVnZ": {
        "cc-pVDZ": "cc-pVDZ", "cc-pVTZ": "cc-pVTZ",
        "cc-pVQZ": "cc-pVQZ", "cc-pV5Z": "cc-pV5Z",
    },
    "Dunning augmented": {
        "aug-cc-pVDZ": "aug-cc-pVDZ",
        "aug-cc-pVTZ": "aug-cc-pVTZ",
        "aug-cc-pVQZ": "aug-cc-pVQZ",
        "aug-cc-pV5Z": "aug-cc-pV5Z",
    },
    "ECP-associated": {"LANL2DZ": "LANL2DZ", "SDD": "SDD"},
}

def flatten(families: dict[str, dict[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for entries in families.values():
        merged.update(entries)
    return merged

METHODS = flatten(METHOD_FAMILIES)
BASIS = flatten(BASIS_FAMILIES)
METHOD_INDEX = tuple(METHOD_FAMILIES)
BASIS_INDEX = tuple(BASIS_FAMILIES)

# Conservative custom one-token choices: no route/section separators or special
# syntax. Their scientific availability cannot be verified without Gaussian.
_CUSTOM = re.compile(r"^[A-Za-z][A-Za-z0-9+().,-]{0,39}$", re.ASCII)
_BLOCKED = {"GEN", "GENECP", "CHKBASIS", "MIXED", "AUTO", "ONIOM"}

def custom_keyword(raw: str, kind: str) -> str:
    keyword = str(raw or "").strip()
    if not _CUSTOM.fullmatch(keyword) or keyword.upper() in _BLOCKED:
        raise ValueError(
            f"Unsupported {kind} keyword format. Enter one Gaussian route keyword "
            "without spaces, '/', '%', or '#'; Gen/GenECP needs /manual."
        )
    return keyword

def paginate(options: dict[str, str], page: int, page_size: int = 8) -> tuple[dict[str, str], int]:
    items = list(options.items())
    page_count = max(1, (len(items) + page_size - 1) // page_size)
    if not 0 <= page < page_count:
        raise ValueError("Out-of-range Gaussian catalogue page.")
    start = page * page_size
    return dict(items[start:start + page_size]), page_count

def family_options(families: dict[str, dict[str, str]]) -> dict[str, str]:
    # Numeric indices keep Telegram callback_data well under 64 bytes.
    return {f"{name} ({len(values)})": str(i) for i, (name, values) in enumerate(families.items())}
