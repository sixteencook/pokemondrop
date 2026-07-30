"""Nommage, arborescence et rétention des captures.

Convention :
    <racine>/YYYY-MM-DD/<site>_<produit>_YYYY-MM-DD_HH-mm-ss.png

La base de données ne stocke QUE le chemin relatif à la racine
(ex. « 2026-08-21/micromania_upc_jour_2026-08-21_14-03-11.png ») : les
fichiers restent sur le disque et la racine peut être déplacée (volume
Railway, autre disque) sans invalider l'historique.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.utils.logger import get_logger

log = get_logger("screenshots.storage")

_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def slugify(value: str, max_length: int = 60) -> str:
    """« Pokémon 30 Ans UPC Jour » → « pokemon_30_ans_upc_jour »."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = _SLUG_CLEAN.sub("_", folded.lower()).strip("_")
    return (slug[:max_length].rstrip("_")) or "produit"


def build_relative_path(
    site: str, product_name: str, when: datetime | None = None, extension: str = "png"
) -> Path:
    """Chemin relatif (dossier du jour + nom de fichier horodaté)."""
    moment = when or datetime.now()
    day = moment.strftime("%Y-%m-%d")
    stamp = moment.strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{slugify(site, 30)}_{slugify(product_name)}_{stamp}.{extension}"
    return Path(day) / filename


def resolve(root: Path, relative: str | Path) -> Path | None:
    """Chemin absolu d'une capture, ou None si la cible sort de la racine.

    Protège le service de fichiers de l'API contre toute traversée de
    répertoire (« ../../.env »). Accepte indifféremment les séparateurs
    « / » et « \\ » : les chemins écrits par une ancienne version Windows
    restent lisibles sous Linux.
    """
    root = root.resolve()
    normalised = str(relative).replace("\\", "/")
    candidate = (root / Path(normalised)).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def prepare_target(root: Path, relative: Path) -> Path:
    """Crée le dossier du jour et retourne le chemin absolu du fichier."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def purge_older_than(root: Path, days: int) -> int:
    """Supprime les dossiers journaliers plus anciens que `days` jours.

    Retourne le nombre de dossiers supprimés. `days <= 0` désactive la purge.
    """
    if days <= 0 or not root.exists():
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        try:
            folder_date = datetime.strptime(folder.name, "%Y-%m-%d").date()
        except ValueError:
            continue  # dossier hors convention : on n'y touche pas
        if folder_date < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    if removed:
        log.ok("Captures : %d dossier(s) de plus de %d jours supprimé(s).", removed, days)
    return removed
