"""Point d'entrée du serveur web (API + dashboard + moteur de surveillance).

Usage :
    python server.py                # écoute sur PORT (défaut 8000)
    uvicorn server:app              # équivalent

Sur Railway, la variable PORT est fournie automatiquement.
"""

from __future__ import annotations

import os

import uvicorn

from src.web.app import create_app

app = create_app()


if __name__ == "__main__":
    # L'objet est passé directement (et non la chaîne « server:app ») :
    # uvicorn réimporterait sinon ce module, construisant l'application
    # une seconde fois.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        log_config=None,  # on garde notre logging maison
    )
