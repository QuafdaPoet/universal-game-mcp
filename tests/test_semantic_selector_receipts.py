import pytest

from ugm.core.types import BoundingBox, NormalizedRect, OCRItem
from ugm.memory.database import DatabaseManager
from ugm.memory.selector_evidence import Receipt, SelectorEvidenceStore, SelectorRecipe, locate_exact_label


def item(text="Production", score=.95, x=.1):
    return OCRItem(text=text, confidence=score, bbox_pixel=BoundingBox(10, 10, 15, 8),
                   bbox_normalized=NormalizedRect(x, .2, .15, .08))


def receipt(n, *, source="live_observation", game="game", state="menu", capture=None, session=None):
    return Receipt(f"{n:032x}", f"{capture or n:064x}", f"{session or n:032x}", game, state, source)


@pytest.fixture
def store(tmp_path):
    db = DatabaseManager(str(tmp_path / "knowledge.db"))
    yield SelectorEvidenceStore(db)
    db.close()


def test_exact_unique_ocr_match_uses_new_frame_geometry():
    assert locate_exact_label("  Ｐroduction ", [item("PRODUCTION", x=.6)]).box.x == .6
    assert locate_exact_label("Production", [item("Production queue")]).status == "not_found"
    assert locate_exact_label("Production", [item(), item(x=.5, score=.4)]).status == "ambiguous"
    assert locate_exact_label("Production", [item(score=.6)]).status == "inconclusive"
    assert locate_exact_label("Production", []).status == "inconclusive"


def test_replay_and_same_attachment_cannot_satisfy_repeat_threshold(store):
    recipe = SelectorRecipe("game", "menu", "open-production", "Production")
    store.record(recipe, receipt(1, session=1), [item()])
    assert not store.record(recipe, receipt(2, capture=1, session=2), [item()]).new_receipt
    assert not store.record(recipe, receipt(1, capture=3, session=2), [item()]).new_receipt
    for n in (3, 4):
        store.record(recipe, receipt(n, session=1), [item()])
    assert store.status(recipe).status == "candidate"
    assert store.record(recipe, receipt(5, session=2), [item()]).status == "repeat_observed"


def test_contradiction_and_recovery_requires_new_independent_evidence(store):
    recipe = SelectorRecipe("game", "menu", "open-production", "production")
    for i in range(1, 4):
        store.record(recipe, receipt(i), [item()])
    assert store.status(recipe).status == "repeat_observed"
    assert store.record(recipe, receipt(4), [item(), item(x=.4)]).status == "contradicted"
    assert store.record(recipe, receipt(5), []).status == "contradicted"
    for i in (6, 7):
        assert store.record(recipe, receipt(i), [item()]).status == "contradicted"
    assert store.record(recipe, receipt(8), [item()]).status == "repeat_observed"
    assert store.status(recipe).contradictions == 1


@pytest.mark.parametrize("source", ["offline_fixture", "dry_run_observation", "legacy_goal_id"])
def test_untrusted_provenance_does_not_create_recipe(store, source):
    recipe = SelectorRecipe("game", "menu", "open-production", "production")
    with pytest.raises(ValueError, match="live"):
        store.record(recipe, receipt(1, source=source), [item()])
    assert store.status(recipe).status == "unseen"


def test_game_state_and_recipe_are_isolated(store):
    recipe = SelectorRecipe("game", "menu", "open-production", "production")
    with pytest.raises(ValueError, match="game and state"):
        store.record(recipe, receipt(1, game="unrelated"), [item()])
    with pytest.raises(ValueError, match="game and state"):
        store.record(recipe, receipt(1, state="different"), [item()])
    store.record(recipe, receipt(1), [item()])
    assert store.status(SelectorRecipe("other", "menu", "open-production", "production")).status == "unseen"
    assert store.status(SelectorRecipe("game", "another", "open-production", "production")).status == "unseen"
    with pytest.raises(ValueError, match="immutable"):
        store.record(SelectorRecipe("game", "menu", "open-production", "research"), receipt(2), [item("research")])


def test_v5_upgrade_keeps_legacy_recipe_separate_and_survives_reopen(tmp_path, monkeypatch):
    from ugm.memory import database

    path = str(tmp_path / "upgrade.db")
    with monkeypatch.context() as v5:
        v5.setattr(database, "CURRENT_SCHEMA_VERSION", 5)
        old = DatabaseManager(path)
        conn = old.get_connection()
        with conn:
            conn.execute("""INSERT INTO selectors_memory
                (game_id, selector_id, state_name, selector_data, last_verified)
                VALUES ('game', 'open-production', 'menu', '{}', 1)""")
        old.close()
    recipe = SelectorRecipe("game", "menu", "open-production", "production")
    for n in (1, 2, 3):
        db = DatabaseManager(path)
        selectors = SelectorEvidenceStore(db)
        assert selectors.status(recipe).observations == n - 1
        selectors.record(recipe, receipt(n), [item()])
        assert db.get_connection().execute("SELECT count(*) FROM selectors_memory").fetchone()[0] == 1
        db.close()
    db = DatabaseManager(path)
    assert SelectorEvidenceStore(db).status(recipe).status == "repeat_observed"
    db.close()


def test_simulation_database_cannot_record_live_receipts(tmp_path):
    db = DatabaseManager(str(tmp_path / "test.db.simulation"), simulation=True)
    try:
        with pytest.raises(ValueError, match="simulation"):
            SelectorEvidenceStore(db).record(SelectorRecipe("game", "menu", "s", "Production"), receipt(1), [item()])
    finally:
        db.close()


def test_old_evidence_expires_and_receipt_capacity_fails_closed(store, monkeypatch):
    from ugm.memory import selector_evidence as module

    now = [100.0]
    monkeypatch.setattr(module, "time", type("Clock", (), {"time": lambda: now[0]}))
    monkeypatch.setattr(module, "MAX_RECEIPTS_PER_RECIPE", 4)
    recipe = SelectorRecipe("game", "menu", "production", "production")
    for n in (1, 2, 3):
        store.record(recipe, receipt(n), [item()])
    assert store.status(recipe).status == "repeat_observed"
    now[0] += module.EVIDENCE_MAX_AGE_S
    assert store.status(recipe).status == "candidate"
    assert store.record(recipe, receipt(4), [item()]).status == "needs_review"
    with pytest.raises(ValueError, match="limit"):
        store.record(recipe, receipt(5), [item("Research")])
    assert store.status(recipe).status == "needs_review"


def test_scope_clear_and_rollback_cannot_retain_selector_authority(tmp_path):
    from ugm.memory.storage import PersistentKnowledgeStorage

    db = DatabaseManager(str(tmp_path / "checkpoint.db"))
    knowledge = PersistentKnowledgeStorage(db)
    ledger = SelectorEvidenceStore(db)
    game = SelectorRecipe("game", "menu", "production", "production")
    other = SelectorRecipe("other", "menu", "production", "production")
    try:
        checkpoint = knowledge.create_checkpoint("game", "before")
        ledger.record(game, receipt(1), [item()])
        ledger.record(other, receipt(2, game="other"), [item()])
        assert knowledge.rollback_checkpoint("game", checkpoint)
        assert ledger.status(game).status == "unseen" and ledger.status(other).status == "candidate"
        knowledge.delete_memory(game_id="other", scope="states")
        assert ledger.status(other).status == "unseen"
    finally:
        db.close()


def test_duplicate_concurrent_receipts_do_not_count_twice(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    recipe = SelectorRecipe("game", "menu", "production", "production")
    start = Barrier(2)

    def write(n):
        try:
            start.wait(timeout=5)
            return store.record(recipe, receipt(n, capture=42), [item()]).new_receipt
        finally:
            store.db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, (1, 2))) == [False, True]
    assert store.status(recipe).observations == 1
