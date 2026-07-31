"""Synchronisation des offres avec la surveillance en temps réel.

Abonné de l'Event Bus : chaque vérification d'un produit surveillé met à
jour l'offre correspondante (prix, disponibilité, statut). C'est ce qui
rend la vue Produit vivante — on voit d'un coup d'œil quel marchand est
disponible.

Aucune offre n'est jamais supprimée : une page en 404 fait simplement
passer l'offre à l'état « introuvable ».
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.events import Event, EventBus, EventType
from src.intelligence.entities import OfferStatus
from src.models import Availability, ProductConfig, ProductSnapshot
from src.utils.logger import get_logger

if TYPE_CHECKING:  # import différé : évite le cycle repositories ↔ intelligence
    from src.repositories import OfferRepository

log = get_logger("intelligence.offers")

#: Disponibilité observée → état de l'offre.
_STATUS_BY_AVAILABILITY = {
    Availability.IN_STOCK: OfferStatus.ACTIVE,
    Availability.PREORDER: OfferStatus.ACTIVE,
    Availability.UNAVAILABLE: OfferStatus.INACTIVE,
    Availability.NOT_LISTED: OfferStatus.NOT_FOUND,
    Availability.UNKNOWN: OfferStatus.ACTIVE,
}


class OfferSyncService:
    """Répercute les vérifications sur les offres du catalogue."""

    def __init__(self, offers: OfferRepository) -> None:
        self._offers = offers

    def attach_to(self, bus: EventBus) -> None:
        bus.subscribe(self._on_check, {EventType.CHECK_COMPLETED})

    async def _on_check(self, event: Event) -> None:
        product: ProductConfig | None = event.payload.get("product")
        snapshot: ProductSnapshot | None = event.payload.get("snapshot")
        if product is None or snapshot is None or not product.uuid:
            return

        offer = await self._offers.by_monitored(product.uuid)
        if offer is None:
            return  # produit surveillé hors catalogue : rien à synchroniser

        await self._offers.upsert(
            product_uuid=offer.product_uuid,
            site=offer.site,
            url=offer.url,
            canonical_url=offer.canonical_url,
            price=snapshot.price,
            availability=snapshot.availability.value,
            monitored_uuid=product.uuid,
        )

        target = (
            OfferStatus.NOT_FOUND if not snapshot.page_exists
            else _STATUS_BY_AVAILABILITY.get(snapshot.availability, OfferStatus.ACTIVE)
        )
        if target is not offer.status:
            await self._offers.set_status(offer.uuid, target)
            log.check(
                "Offre %s (%s) : %s → %s",
                offer.site, product.name, offer.status.value, target.value,
            )
