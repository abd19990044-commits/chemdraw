from __future__ import annotations

"""Production bootstrap.

The original combined ORCA + Chemistry application is preserved byte-for-byte
in ``legacy_app.py``. This bootstrap installs the publication-grade renderer
and automatic stoichiometric balancer into the Chemistry Drawing Bot only,
then starts the unchanged combined app.
"""

import legacy_app as core
from publication_entry_balanced import install_publication_renderer

install_publication_renderer(core)

# Re-export the Flask application for platforms/tools that import ``app:app``.
app = core.app

if __name__ == "__main__":
    core.main()
