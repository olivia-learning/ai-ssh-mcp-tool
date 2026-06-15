import sys

from . import __version__
from .server import main


if __name__ == "__main__":
    if "--version" in sys.argv:
        print(f"ai-ssh-mcp {__version__}")
        raise SystemExit(0)
    main()
