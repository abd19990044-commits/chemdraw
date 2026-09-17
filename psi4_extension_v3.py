from __future__ import annotations

"""Geometry-safe Psi4 production overlay.

The v2 scientific wizard is retained unchanged, but every generated Psi4 input
now passes through the shared strict MOL/SDF/XYZ normalizer before Psithon is
written.
"""

from typing import Any

import psi4_extension_v2 as v2
from geometry_normalizer import normalized_data_copy


_ORIGINAL_GENERATOR = v2.generate_psi4_input_v2


def generate_psi4_input_v3(data: dict[str, Any]) -> str:
    safe = normalized_data_copy(data)
    return _ORIGINAL_GENERATOR(safe)


def install_psi4_extension_v3(core) -> None:
    # v2.install_psi4_extension_v2() assigns its generator into the v1 wizard
    # at install time. Replace that module-level symbol first so both direct
    # generation and Telegram wizard generation use the strict geometry path.
    v2.generate_psi4_input_v2 = generate_psi4_input_v3
    v2.install_psi4_extension_v2(core)
    core.generate_psi4_input = generate_psi4_input_v3
    core.orca_logger.info("Psi4 v3 geometry-safe input generator installed.")
