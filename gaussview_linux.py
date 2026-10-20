"""Convert GaussView Windows file references for Gaussian 16 on Linux.

Only platform-dependent filenames are rewritten. Route sections, molecular
specifications, charge/multiplicity, resources and all other Link 0 directives
are retained byte-for-byte after decoding/newline normalization.

This module prepares *input text*, not binary .chk/.rwf dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


FILE_DIRECTIVES = {"chk", "oldchk", "schk", "oldmatrix", "oldrawmatrix"}
SCRATCH_DIRECTIVES = {"rwf", "int", "d2e"}
LINK0 = re.compile(r"^(\s*%([A-Za-z][A-Za-z0-9]*)\s*=\s*)(.*)$")
DEPENDENCY_DIRECTIVES = {"oldchk", "oldmatrix", "oldrawmatrix"}
SIZE_TOKEN = re.compile(r"^(?:-1|0|[1-9]\d*(?:[KMGT]?[BW])?)$", re.I)
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ConversionReport:
    content: str
    converted_directives: tuple[str, ...]
    required_files: tuple[str, ...]
    warnings: tuple[str, ...]


def decode_gaussview_text(raw: bytes) -> str:
    """Accept UTF-8, BOM-marked UTF-16 and common GaussView Windows ANSI text."""
    if not raw:
        raise ValueError("The uploaded Gaussian input is empty.")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ValueError("Invalid UTF-16 Gaussian input.") from exc
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # GaussView Windows exports may encode a non-ASCII title in CP1252.
            try:
                text = raw.decode("cp1252")
            except UnicodeDecodeError as exc:
                raise ValueError("Input is not UTF-8 or Windows-1252 text.") from exc
    if "\x00" in text or any(ord(c) < 32 and c not in "\r\n\t\f" for c in text):
        raise ValueError("Input contains binary or control characters, not Gaussian text.")
    return text


def _windows_path(value: str) -> bool:
    candidate = value.strip().strip('"').strip("'")
    return bool(WINDOWS_DRIVE.match(candidate) or "\\" in candidate or candidate.startswith("//"))


def _portable_path(value: str, *, directory_allowed: bool = False) -> str:
    candidate = value.strip().strip('"').strip("'")
    if not _windows_path(candidate):
        return value
    unified = candidate.replace("\\", "/")
    if unified.endswith("/"):
        if directory_allowed:
            return "./"
        raise ValueError("A Gaussian checkpoint/matrix directive must name a file, not a Windows directory.")
    base = unified.rsplit("/", 1)[-1]
    # Gaussian Linux filename arguments are portable and shell-friendly without
    # drive letters, directory separators, whitespace or shell metacharacters.
    base = re.sub(r"[^A-Za-z0-9._+-]", "_", base).strip("._")
    if not base or base in {".", ".."}:
        raise ValueError("Cannot derive a portable Linux filename from a Windows path.")
    return base


def convert_gaussview_to_linux(text: str) -> ConversionReport:
    """Rewrite only Windows paths in recognized Gaussian input file directives.

    Keeps multi-segment %RWF/%Int/%D2E size tokens, Link1 job steps,
    custom basis/ECP sections and route keywords untouched. Windows @file
    references are made relative but require the file to be uploaded separately.
    """
    if not isinstance(text, str) or not text.strip() or "\x00" in text:
        raise ValueError("Empty or binary Gaussian input.")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    converted: list[str] = []
    dependencies: list[str] = []
    warnings: list[str] = []
    targets: dict[str, str] = {}
    in_link0 = True
    lines: list[str] = []

    def map_filename(value: str, *, directory_allowed: bool = False) -> str:
        result = _portable_path(value, directory_allowed=directory_allowed)
        if result != value:
            original = value.strip().strip('"').strip("'").replace("\\", "/").casefold()
            previous = targets.get(result.casefold())
            if previous is not None and previous != original:
                raise ValueError(
                    "Two different Windows paths would become the same Linux filename "
                    f"({result}). Rename one source file or edit its path before uploading."
                )
            targets[result.casefold()] = original
        return result

    for line in normalized.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        body = line[:-1] if ending else line
        stripped = body.strip()
        if stripped.casefold() == "--link1--":
            in_link0 = True
            lines.append(line)
            continue
        if in_link0 and stripped.startswith("#"):
            in_link0 = False

        matched = LINK0.match(body) if in_link0 else None
        if matched:
            prefix, command, raw_value = matched.groups()
            key = command.casefold()
            value = raw_value.strip()
            revised = value
            if key in FILE_DIRECTIVES:
                if key in {"oldmatrix", "oldrawmatrix"}:
                    # Gaussian also permits (filename,i4lab) matrix arguments.
                    parenthesized = value.startswith("(") and value.endswith(")")
                    inner = value[1:-1] if parenthesized else value
                    filename, separator, modifier = inner.partition(",")
                    mapped = map_filename(filename)
                    revised = mapped + (separator + modifier if separator else "")
                    if parenthesized:
                        revised = "(" + revised + ")"
                    dependency_name = mapped
                else:
                    revised = map_filename(value)
                    dependency_name = revised
                if key in DEPENDENCY_DIRECTIVES:
                    dependencies.append(dependency_name)
            elif key in SCRATCH_DIRECTIVES:
                # Gaussian: %RWF=location,size,location,size ...
                # Keep every numeric size and its ordering exactly unchanged.
                parts = value.split(",")
                for i in range(0, len(parts), 2):
                    parts[i] = map_filename(parts[i], directory_allowed=True)
                revised = ",".join(parts)
                if len(parts) > 1:
                    for token in parts[1::2]:
                        if not SIZE_TOKEN.fullmatch(token.strip()):
                            warnings.append(
                                f"Verify %{command} segmented scratch size specification on Linux."
                            )
            if revised != value:
                converted.append("%" + command)
                lines.append(prefix + revised + ending)
            else:
                lines.append(line)
            continue

        # Gaussian external basis/ECP coordinates may use an @filename line.
        # Do not touch arbitrary route keywords or molecule data.
        if stripped.startswith("@"):
            m = re.match(r"^(\s*@)(.+?)(\s*)$", body)
            if m:
                file_spec = m.group(2)
                suffix = "/N" if file_spec.upper().endswith("/N") else ""
                source = file_spec[:-2] if suffix else file_spec
                if _windows_path(source):
                    mapped = map_filename(source)
                    dependencies.append(mapped)
                    converted.append("@external-file")
                    lines.append(m.group(1) + mapped + suffix + m.group(3) + ending)
                    continue

        if re.search(r"[A-Za-z]:[\\/]|\\\\", body):
            warnings.append(
                "An additional Windows path appears outside recognized Gaussian file "
                "directives; it was left unchanged to protect calculation instructions."
            )
        lines.append(line)

    content = "".join(lines)
    if re.search(r"(?i)\b(?:Geom\s*=\s*(?:AllCheck|Check)|Guess\s*=\s*Read|"
                 r"(?:Opt|IRC)\s*=\s*Restart)\b", content):
        warnings.append("This job may need an existing checkpoint to restart/read geometry/orbitals.")
    if any(name.casefold() in {"%rwf", "%int", "%d2e"} for name in converted):
        warnings.append("Windows scratch paths were changed to relative Linux paths; check available disk space.")
    if dependencies:
        warnings.append("Transfer required .chk/matrix/basis files to the Linux working directory separately.")
    return ConversionReport(
        content=content,
        converted_directives=tuple(dict.fromkeys(converted)),
        required_files=tuple(dict.fromkeys(dependencies)),
        warnings=tuple(dict.fromkeys(warnings)),
    )
