from __future__ import annotations

"""Production bootstrap.

The original combined ORCA + Chemistry application is preserved byte-for-byte
in ``legacy_app.py``. Production overlays add the publication-grade chemistry
renderer and extend the quantum-input bot with professional ORCA 6.1 and Psi4
Psithon (.dat) workflows while preserving the legacy implementation.
"""

import legacy_app as core
from geometry_normalizer import install_geometry_normalizer
from publication_entry_advanced import install_publication_renderer
from psi4_extension_v3 import install_psi4_extension_v3

# Geometry normalization is installed before the quantum-input wizard so every
# PubChem, MOL/SDF and XYZ path is canonicalized to Element/X/Y/Z coordinates
# before either ORCA or Psi4 input text is written.
install_geometry_normalizer(core)
install_publication_renderer(core)
install_psi4_extension_v3(core)

# Re-export the Flask application for platforms/tools that import ``app:app``.
app = core.app

if __name__ == "__main__":
    core.main()
