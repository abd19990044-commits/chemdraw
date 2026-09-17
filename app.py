from __future__ import annotations

"""Production bootstrap.

The original combined ORCA + Chemistry application is preserved byte-for-byte
in ``legacy_app.py``. Production overlays add the publication-grade chemistry
renderer and extend the quantum-input bot with professional ORCA 6.1 and Psi4
Psithon (.dat) workflows while preserving the legacy implementation.
"""

import legacy_app as core
from publication_entry_advanced import install_publication_renderer
from psi4_extension_v2 import install_psi4_extension_v2

install_publication_renderer(core)
install_psi4_extension_v2(core)

# Re-export the Flask application for platforms/tools that import ``app:app``.
app = core.app

if __name__ == "__main__":
    core.main()
