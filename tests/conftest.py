import sys
from pathlib import Path

# Permet d'importer `src` depuis les tests sans installation du paquet.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
