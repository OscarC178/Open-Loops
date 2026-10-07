"""`python3 -m openloops <command>`: the developer console (cli.py). `python3 -m openloops.app` is still the server."""
import sys

from .cli import main

sys.exit(main())
