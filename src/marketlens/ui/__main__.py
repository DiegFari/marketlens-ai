"""Launch the UI with ``python -m marketlens.ui [streamlit options]``.

Streamlit needs a script path; resolving it here means the same command works from a
source checkout and from an installed wheel inside the container.
"""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.web import cli as streamlit_cli


def main() -> None:
    app_path = Path(__file__).with_name("app.py")
    sys.argv = ["streamlit", "run", str(app_path), *sys.argv[1:]]
    sys.exit(streamlit_cli.main())


if __name__ == "__main__":
    main()
