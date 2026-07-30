"""Persistance de l'état des produits entre deux exécutions.

Chaque produit possède un fichier JSON dans data/state/ contenant son
dernier snapshot. Cela permet :
  - d'éviter les notifications répétitives (on ne notifie qu'un changement) ;
  - de ne PAS alerter au premier lancement (baseline silencieuse) ;
  - de reprendre proprement après un redémarrage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.models import ProductSnapshot


class StateStore:
    """Stockage JSON, un fichier par produit (clé = ProductConfig.key)."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def load(self, key: str) -> Optional[ProductSnapshot]:
        """Retourne le dernier snapshot connu, ou None au premier lancement."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ProductSnapshot.from_dict(data)
        except (json.JSONDecodeError, ValueError, KeyError):
            # Fichier corrompu : on repart d'une baseline propre.
            return None

    def save(self, key: str, snapshot: ProductSnapshot) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)  # écriture atomique
