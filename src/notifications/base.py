"""Interface commune à tous les canaux de notification.

Pour ajouter un canal (Discord, Email, SMS…) :
  1. créer src/notifications/<canal>.py héritant de BaseNotifier ;
  2. implémenter `send(event, screenshot=None)` ;
  3. l'ajouter au NotificationManager dans main.py / src/web/app.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Optional

from src.models import ChangeEvent


class BaseNotifier(ABC):
    """Contrat d'un canal de notification."""

    channel_name: ClassVar[str] = ""

    @abstractmethod
    async def send(
        self, event: ChangeEvent, screenshot: Optional[Path] = None
    ) -> bool:
        """Envoie l'alerte, avec sa capture d'écran si elle est disponible.

        Retourne True si l'alerte a été délivrée (au moins un destinataire).
        Un canal qui ne gère pas les images doit ignorer `screenshot`.
        """
