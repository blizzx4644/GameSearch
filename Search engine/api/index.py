import sys
from pathlib import Path

# Ajouter le répertoire parent au chemin Python pour les imports
sys.path.append(str(Path(__file__).parent.parent))

from backend.app import app

__all__ = ["app"]
