"""Analyse HTML spécifique à Micromania (point d'extension).

Tant que les fiches Pokémon 30e anniversaire n'existent pas, l'analyse
générique par mots-clés (GenericHtmlMonitor.parse) suffit et AUCUN code
spécifique n'est écrit ici — on n'invente pas de structure de page.

Le jour où le HTML réel sera observable, si l'analyse générique se révèle
insuffisante, il suffira d'implémenter ici :

    from src.models import ProductConfig, ProductSnapshot

    def parse(html: str, product: ProductConfig) -> ProductSnapshot:
        ...  # sélecteurs réels de la fiche Micromania

puis de surcharger `parse()` dans monitor.py pour déléguer à cette
fonction. Rien d'autre à modifier dans le projet.
"""
