"""Canal de notification Telegram (prioritaire).

API Bot HTTP officielle, sans dépendance lourde :
  - sendPhoto lorsqu'une capture d'écran est disponible (le message devient
    la légende de la photo) ;
  - sendMessage sinon, ET en repli automatique si l'envoi de la photo échoue
    (fichier trop lourd, réseau, format refusé…) — une alerte n'est jamais perdue.

Multi-destinations : chaque Chat ID configuré (compte personnel, groupe
privé, second compte) reçoit la même alerte ; un destinataire en échec
n'empêche pas les autres.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path
from typing import ClassVar, Optional, Sequence

import httpx

from src.models import ChangeEvent, ChangeType
from src.notifications.base import BaseNotifier
from src.utils.logger import get_logger

log = get_logger("notifications.telegram")

#: Limite Telegram pour une légende de photo (1024 caractères).
CAPTION_LIMIT = 1024

#: Limite de taille d'une photo envoyée par URL/multipart (10 Mo).
PHOTO_SIZE_LIMIT = 10 * 1024 * 1024

#: Longueur maximale d'une valeur affichée dans une transition.
VALUE_PREVIEW_LIMIT = 140


def _short(value: Optional[str]) -> str:
    """Valeur lisible dans un message : tronquée proprement si trop longue."""
    if not value:
        return "—"
    collapsed = " ".join(value.split())
    if len(collapsed) <= VALUE_PREVIEW_LIMIT:
        return collapsed
    return collapsed[: VALUE_PREVIEW_LIMIT - 1] + "…"


_CHANGE_LABELS: dict[ChangeType, str] = {
    ChangeType.PRODUCT_APPEARED: "🆕 Fiche produit en ligne",
    ChangeType.PRICE_APPEARED: "💶 Prix affiché",
    ChangeType.PRICE_CHANGED: "💶 Prix modifié",
    ChangeType.PREORDER_OPENED: "🟢 PRÉCOMMANDE OUVERTE",
    ChangeType.BACK_IN_STOCK: "🟢 RETOUR EN STOCK",
    ChangeType.BUTTON_CHANGED: "🔄 Bouton modifié",
    ChangeType.STATUS_CHANGED: "🔄 Statut modifié",
    ChangeType.PAGE_CHANGED: "🔄 Page modifiée",
}


class TelegramNotifier(BaseNotifier):
    channel_name: ClassVar[str] = "telegram"

    def __init__(
        self, bot_token: str, chat_ids: Sequence[str], client: httpx.AsyncClient
    ) -> None:
        self._chat_ids = list(chat_ids)
        self._client = client
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send(
        self, event: ChangeEvent, screenshot: Optional[Path] = None
    ) -> bool:
        """Envoie l'alerte à tous les destinataires.

        Retourne True si AU MOINS un destinataire a reçu le message.
        """
        message = self._format(event, with_screenshot=screenshot is not None)
        return await self._deliver(message, self._usable_photo(screenshot))

    async def send_discovery(
        self,
        title: str,
        site_label: str,
        url: str,
        price: Optional[str] = None,
        imported: bool = False,
        image_url: Optional[str] = None,
    ) -> bool:
        """Annonce une fiche inédite repérée par la couche Découverte."""
        return await self._deliver(
            self._format_discovery(title, site_label, url, price, imported), None
        )

    async def _deliver(self, message: str, photo: Optional[Path]) -> bool:
        """Transport commun : photo si disponible, repli texte systématique."""
        delivered = 0
        for chat_id in self._chat_ids:
            sent = False
            if photo is not None:
                sent = await self._send_photo(chat_id, photo, message)
                if not sent:
                    log.error(
                        "Photo refusée pour le chat %s — repli sur le message texte.",
                        chat_id,
                    )
            if not sent:
                sent = await self._send_message(chat_id, message)
            delivered += int(sent)

        if 0 < delivered < len(self._chat_ids):
            log.error(
                "Message partiellement délivré : %d/%d destinataire(s) Telegram.",
                delivered, len(self._chat_ids),
            )
        return delivered > 0

    # ------------------------------------------------------------------ #
    # Transport                                                           #
    # ------------------------------------------------------------------ #

    def _usable_photo(self, screenshot: Optional[Path]) -> Optional[Path]:
        """Valide la capture avant tout envoi (existence, taille)."""
        if screenshot is None:
            return None
        if not screenshot.is_file():
            log.error("Capture introuvable, envoi en texte seul : %s", screenshot)
            return None
        if screenshot.stat().st_size > PHOTO_SIZE_LIMIT:
            log.error(
                "Capture trop volumineuse (%.1f Mo > 10 Mo), envoi en texte seul.",
                screenshot.stat().st_size / (1024 * 1024),
            )
            return None
        return screenshot

    async def _send_photo(self, chat_id: str, photo: Path, caption: str) -> bool:
        try:
            with photo.open("rb") as handle:
                response = await self._client.post(
                    f"{self._base_url}/sendPhoto",
                    data={
                        "chat_id": chat_id,
                        "caption": caption[:CAPTION_LIMIT],
                        "parse_mode": "HTML",
                    },
                    files={"photo": (photo.name, handle, "image/png")},
                    timeout=60,  # l'upload est plus lent qu'un simple message
                )
            if response.status_code == 200:
                return True
            log.error(
                "sendPhoto refusé pour le chat %s (HTTP %s) : %s",
                chat_id, response.status_code, response.text[:200],
            )
            return False
        except (httpx.HTTPError, OSError) as exc:
            log.error("sendPhoto impossible (chat %s) : %s", chat_id, exc)
            return False

    async def _send_message(self, chat_id: str, message: str) -> bool:
        try:
            response = await self._client.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=15,
            )
            if response.status_code == 200:
                return True
            log.error(
                "Telegram a refusé le message pour le chat %s (HTTP %s) : %s",
                chat_id, response.status_code, response.text[:200],
            )
            return False
        except httpx.HTTPError as exc:
            log.error("Envoi Telegram impossible (chat %s) : %s", chat_id, exc)
            return False

    # ------------------------------------------------------------------ #
    # Mise en forme                                                       #
    # ------------------------------------------------------------------ #

    def _format(self, event: ChangeEvent, with_screenshot: bool = False) -> str:
        label = _CHANGE_LABELS.get(event.change_type, event.change_type.value)
        lines = [
            "🚨 <b>ALERTE DROP</b>",
            "",
            f"<b>Produit :</b> {escape(event.product.name)}",
            f"<b>Site :</b> {escape(event.product.site.capitalize())}",
            f"<b>Heure :</b> {datetime.now().strftime('%H:%M:%S')}",
            f"<b>Événement :</b> {escape(label)}",
        ]
        if event.old_value or event.new_value:
            # Les libellés de boutons peuvent être longs : on tronque AVANT
            # la mise en forme HTML, pour ne jamais couper une balise.
            lines.append(
                f"<b>Changement :</b> {escape(_short(event.old_value))} → "
                f"{escape(_short(event.new_value))}"
            )
        if event.snapshot.price:
            lines.append(f"<b>Prix :</b> {escape(event.snapshot.price)}")
        lines.append(f"<b>URL :</b> {escape(event.product.url)}")
        if with_screenshot:
            lines += ["", "📸 Capture d'écran jointe"]
        return "\n".join(lines)

    def _format_discovery(
        self,
        title: str,
        site_label: str,
        url: str,
        price: Optional[str],
        imported: bool,
    ) -> str:
        lines = [
            "🆕 <b>NOUVEAU PRODUIT DÉTECTÉ</b>",
            "",
            f"<b>Site :</b> {escape(site_label)}",
            f"<b>Produit :</b> {escape(title)}",
        ]
        if price:
            lines.append(f"<b>Prix :</b> {escape(price)}")
        lines += [
            f"<b>Heure :</b> {datetime.now().strftime('%H:%M:%S')}",
            f"<b>URL :</b> {escape(url)}",
            "",
            "✅ Surveillance démarrée automatiquement"
            if imported
            else "⏳ En attente de validation dans le dashboard",
        ]
        return "\n".join(lines)
