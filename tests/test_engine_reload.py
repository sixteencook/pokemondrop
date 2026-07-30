"""Tests de la réconciliation à chaud du moteur.

On ne teste pas les requêtes HTTP (pas d'URL réelle) : _watch est remplacé
par une boucle inerte, seul le mécanisme démarrage/arrêt/redémarrage des
watchers selon la configuration courante est vérifié.
"""

import asyncio

import httpx
import pytest

from src.core import EventBus, MonitorEngine
from src.models import GlobalSettings
from src.monitors import create_registry
from src.repositories import SnapshotRepository
from tests.helpers import make_db, make_product

pytestmark = pytest.mark.asyncio


async def _idle_watch(product):
    await asyncio.Event().wait()  # boucle inerte, annulable


async def _make_engine(tmp_path, provider):
    db = await make_db(tmp_path)
    engine = MonitorEngine(
        registry=create_registry(httpx.AsyncClient()),
        bus=EventBus(),
        snapshots=SnapshotRepository(db.session_factory),
        settings=GlobalSettings(),
        product_provider=provider,
    )
    engine._watch = _idle_watch  # pas de HTTP dans ces tests
    return db, engine


async def test_reconcile_starts_and_stops_watchers(tmp_path):
    products = [make_product(uuid="u1")]

    async def provider():
        return products

    db, engine = await _make_engine(tmp_path, provider)

    await engine._reconcile()
    assert engine.active_count == 1

    products.clear()  # produit supprimé depuis le dashboard
    await engine._reconcile()
    assert engine.active_count == 0
    await db.dispose()


async def test_reconcile_restarts_on_config_change(tmp_path):
    products = [make_product(uuid="u1", check_interval=60)]

    async def provider():
        return products

    db, engine = await _make_engine(tmp_path, provider)
    await engine._reconcile()
    first_task = engine._watchers["u1"][1]

    products[0] = make_product(uuid="u1", check_interval=30)  # intervalle modifié
    await engine._reconcile()
    assert engine.active_count == 1
    assert engine._watchers["u1"][1] is not first_task  # watcher redémarré

    products[0] = make_product(uuid="u1", check_interval=30)  # config identique
    second_task = engine._watchers["u1"][1]
    await engine._reconcile()
    assert engine._watchers["u1"][1] is second_task  # pas de redémarrage inutile
    await db.dispose()


async def test_reconcile_ignores_disabled_and_empty_url(tmp_path):
    async def provider():
        return [
            make_product(uuid="u1", enabled=False),
            make_product(uuid="u2", url=""),
        ]

    db, engine = await _make_engine(tmp_path, provider)
    await engine._reconcile()
    assert engine.active_count == 0
    await db.dispose()


async def test_reconcile_survives_unknown_site(tmp_path):
    async def provider():
        return [make_product(uuid="u1", site="site-inconnu"),
                make_product(uuid="u2")]

    db, engine = await _make_engine(tmp_path, provider)
    await engine._reconcile()  # ne doit pas lever
    assert engine.active_count == 1  # u2 tourne malgré u1 invalide
    await db.dispose()
