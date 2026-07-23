from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    project_root = Path(__file__).resolve().parent
    src_path = project_root / "src"
    sys.path.insert(0, str(src_path))

    from pi_translator.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
