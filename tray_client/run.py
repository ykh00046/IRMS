"""Entry point for the IRMS Notice tray client.

Kept at the project root so PyInstaller freezes it as ``__main__`` outside
the ``src`` package. That lets modules inside ``src`` keep using relative
imports (``from .config import ...``), which fail when ``src/main.py`` is
invoked directly as the frozen entry point.
"""

from src.main import main

if __name__ == "__main__":
    main()
