"""Allow ``python -m contractfuzz``."""

import sys

from contractfuzz.cli import main

if __name__ == "__main__":
    sys.exit(main())
