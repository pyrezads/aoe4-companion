"""Entry point of the packaged core (aoe4-core.exe). Same as: python main.py --core ..."""
import sys

import main

if "--core" not in sys.argv and "--test" not in sys.argv:
    sys.argv.insert(1, "--core")
main.main()
