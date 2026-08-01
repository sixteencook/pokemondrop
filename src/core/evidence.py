"""Archivage du HTML ayant motivé une décision importante.

Quand une alerte part, on conserve la page telle qu'elle a été analysée.
Cela permet de répondre plus tard à « pourquoi cette alerte ? » et de
rejouer l'analyse hors ligne si un doute subsiste.

Même convention que les captures d'écran :

    <racine>/YYYY-MM-DD/<site>_<produit>_YYYY-MM-DD_HH-mm-ss.html

Seules les décisions marquantes sont archivées (retour en stock, ouverture
de précommande, apparition de prix…) : conserver chaque vérification
saturerait le disque sans rien apprendre.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.models import ChangeEvent, ProductConfig
from src.utils.logger import get_logger
from src.utils.text import slugify

log = get_logger("evidence")

#: Taille maximale conservée : au-delà, on tronque plutôt que de saturer.
MAX_EVIDENCE_BYTES = 3 * 1024 * 1024


def build_relative_path(
    site: str, product_name: str, when: Optional[datetime] = None
) -> Path:
    moment = when or datetime.now()
    day = moment.strftime("%Y-%m-%d")
    stamp = moment.strftime("%Y-%m-%d_%H-%M-%S")
    return Path(day) / f"{slugify(site, 30)}_{slugify(product_name)}_{stamp}.html"


def store(
    root: Path,
    product: ProductConfig,
    change: ChangeEvent,
    html: Optional[str],
) -> Optional[str]:
    """Archive la page et retourne son chemin relatif (POSIX), ou None.

    N'échoue jamais : un problème d'écriture ne doit pas empêcher l'alerte.
    """
    if not html or not change.is_important:
        return None

    relative = build_relative_path(product.site, product.name)
    target = root / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = html[:MAX_EVIDENCE_BYTES]
        header = (
            f"<!-- Drop Monitor — preuve d'alerte\n"
            f"     produit : {product.name}\n"
            f"     site    : {product.site}\n"
            f"     url     : {product.url}\n"
            f"     évènement : {change.change_type.value} "
            f"({change.old_value or '—'} → {change.new_value or '—'})\n"
            f"     analysé le : {datetime.now(timezone.utc).isoformat()}\n"
            f"-->\n"
        )
        target.write_text(header + payload, encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error("Archivage de la preuve impossible (%s) : %s", product.name, exc)
        return None

    log.check("Preuve archivée : %s (%.0f Ko)", relative, len(payload) / 1024)
    return relative.as_posix()


def resolve(root: Path, relative: str | Path) -> Optional[Path]:
    """Chemin absolu d'une preuve, ou None si la cible sort de la racine."""
    root = root.resolve()
    candidate = (root / Path(str(relative).replace("\\", "/"))).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def purge_older_than(root: Path, days: int) -> int:
    """Supprime les dossiers journaliers trop anciens."""
    import shutil

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
            continue
        if folder_date < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    if removed:
        log.ok("Preuves : %d dossier(s) de plus de %d jours supprimés.", removed, days)
    return removed
