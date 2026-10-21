"""Allow `python -m photo_sorter ...` (works even if the console script is not on PATH)."""
import sys

from .cli import main

sys.exit(main())
