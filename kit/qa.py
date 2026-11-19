#!/usr/bin/env python3
"""Entry point of the QA agent kit's helper. Standard library only: python3 .qa-agent/kit/qa.py <command>"""

import sys

if sys.version_info < (3, 9):
    sys.stderr.write("error: the QA agent helper needs Python 3.9 or newer; this is %s\n" % sys.version.split()[0])
    sys.exit(2)

sys.dont_write_bytecode = True  # the installed kit stays exactly as it was installed

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
