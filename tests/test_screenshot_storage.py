"""Nommage, arborescence, sécurité des chemins et rétention des captures."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.services.screenshots import storage


def test_slugify_folds_accents_and_spaces():
    assert storage.slugify("Pokémon 30 Ans UPC Jour") == "pokemon_30_ans_upc_jour"
    assert storage.slugify("Édition Collector !!") == "edition_collector"
    assert storage.slugify("") == "produit"


def test_relative_path_follows_convention():
    when = datetime(2026, 8, 21, 14, 3, 11)
    path = storage.build_relative_path("micromania", "Pokémon UPC Jour", when)
    assert path == Path("2026-08-21") / "micromania_pokemon_upc_jour_2026-08-21_14-03-11.png"


def test_prepare_target_creates_day_folder(tmp_path):
    relative = storage.build_relative_path("micromania", "Test", datetime(2026, 8, 21))
    target = storage.prepare_target(tmp_path, relative)
    assert target.parent.is_dir()
    assert target.parent.name == "2026-08-21"


def test_resolve_rejects_path_traversal(tmp_path):
    (tmp_path / "secret.txt").write_text("données", encoding="utf-8")
    root = tmp_path / "screenshots"
    root.mkdir()
    assert storage.resolve(root, "../secret.txt") is None
    assert storage.resolve(root, "inexistant.png") is None


def test_resolve_accepts_real_file(tmp_path):
    day = tmp_path / "2026-08-21"
    day.mkdir()
    image = day / "capture.png"
    image.write_bytes(b"PNG")
    assert storage.resolve(tmp_path, "2026-08-21/capture.png") == image


def test_resolve_accepts_windows_separators(tmp_path):
    """Portabilité : un chemin écrit sous Windows reste lisible sous Linux."""
    day = tmp_path / "2026-08-21"
    day.mkdir()
    image = day / "capture.png"
    image.write_bytes(b"PNG")
    assert storage.resolve(tmp_path, "2026-08-21\\capture.png") == image


def test_purge_removes_only_old_day_folders(tmp_path):
    today = datetime.now(timezone.utc)
    recent = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    ancient = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    for name in (recent, ancient, "pas-une-date"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "x.png").write_bytes(b"PNG")

    assert storage.purge_older_than(tmp_path, 90) == 1
    assert (tmp_path / recent).exists()
    assert not (tmp_path / ancient).exists()
    assert (tmp_path / "pas-une-date").exists()  # jamais touché


def test_purge_disabled_when_zero_days(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=500)).strftime("%Y-%m-%d")
    (tmp_path / old).mkdir()
    assert storage.purge_older_than(tmp_path, 0) == 0
    assert (tmp_path / old).exists()
