from pathlib import Path

import pytest
import torch

from ord_ai.boss import BOSS_SIZE
from ord_ai.catalog import Action, Catalog, UnitType
from ord_ai.checkpoint import Metadata, load_checkpoint, migrate_checkpoint, save_checkpoint
from ord_ai.learning import ActorCritic
from ord_ai.observation import input_size
from ord_ai.protocol import ProtocolError
from ord_ai.treasure import TREASURE_SIZE

UNITS = (UnitType(raw_id=1, name="unit"),)
OLD_ACTIONS = (Action(code=0, name="wait"), Action(code=2, name="roll"), Action(code=1, name="buy"))
OLD = Catalog(map_sha256="map", units=UNITS, actions=OLD_ACTIONS)
# New actions sit between old ones, so the heads must be reindexed by wire code.
NEW = Catalog(
    map_sha256="map",
    units=UNITS,
    actions=(OLD_ACTIONS[0], Action(code=5, name="chest"), OLD_ACTIONS[1], OLD_ACTIONS[2]),
)
OLD_SIZE = input_size(OLD) - TREASURE_SIZE - BOSS_SIZE


def _old_checkpoint(directory: Path, size: int = OLD_SIZE, catalog: Catalog = OLD) -> ActorCritic:
    torch.set_rng_state(torch.Generator().manual_seed(3).get_state())
    model = ActorCritic(size, len(catalog.actions))
    metadata = Metadata(
        catalog_fingerprint=catalog.fingerprint,
        steps=42,
        input_size=size,
        action_count=len(catalog.actions),
    )
    save_checkpoint(directory, model, metadata)
    _ = (directory / "catalog.json").write_text(catalog.model_dump_json(), "utf-8")
    return model


def test_migrated_checkpoint_keeps_old_outputs_while_chest_features_are_zero(
    tmp_path: Path,
) -> None:
    # Given
    old_model = _old_checkpoint(tmp_path / "old")
    observations = torch.rand(16, OLD_SIZE, generator=torch.Generator().manual_seed(5))
    padded = torch.cat((observations, torch.zeros(16, TREASURE_SIZE + BOSS_SIZE)), dim=1)

    # When
    metadata = migrate_checkpoint(tmp_path / "old", tmp_path / "new", NEW)
    migrated = load_checkpoint(tmp_path / "new", NEW, "cpu")

    # Then
    with torch.no_grad():
        old_logits, old_values = old_model.evaluate(observations)
        new_logits, new_values = migrated.evaluate(padded)
    codes = [action.code for action in NEW.actions]
    for old_index, action in enumerate(OLD.actions):
        new_column = new_logits[:, codes.index(action.code)]
        torch.testing.assert_close(new_column, old_logits[:, old_index], rtol=0, atol=1e-6)
    torch.testing.assert_close(new_values, old_values, rtol=0, atol=1e-6)
    mean_bias = old_model.policy_head.bias.detach().mean().expand(16)
    torch.testing.assert_close(new_logits[:, codes.index(5)], mean_bias, rtol=0, atol=1e-6)
    assert (metadata.steps, metadata.action_count) == (42, len(NEW.actions))
    assert metadata.migrated_from is not None
    assert metadata.migrated_from.catalog_fingerprint == OLD.fingerprint
    saved = Catalog.model_validate_json((tmp_path / "new" / "catalog.json").read_text("utf-8"))
    assert saved.fingerprint == NEW.fingerprint


def test_a_pre_boss_checkpoint_gains_only_the_boss_columns(tmp_path: Path) -> None:
    # Given: a checkpoint of the current catalog trained before the boss features.
    size = input_size(NEW) - BOSS_SIZE
    old_model = _old_checkpoint(tmp_path / "old", size, NEW)
    observations = torch.rand(8, size, generator=torch.Generator().manual_seed(7))

    # When
    _ = migrate_checkpoint(tmp_path / "old", tmp_path / "new", NEW)
    migrated = load_checkpoint(tmp_path / "new", NEW, "cpu")

    # Then
    with torch.no_grad():
        old_logits, old_values = old_model.evaluate(observations)
        new_logits, new_values = migrated.evaluate(
            torch.cat((observations, torch.zeros(8, BOSS_SIZE)), dim=1)
        )
    torch.testing.assert_close(new_logits, old_logits, rtol=0, atol=1e-6)
    torch.testing.assert_close(new_values, old_values, rtol=0, atol=1e-6)


@pytest.mark.parametrize(
    "actions",
    [
        (OLD_ACTIONS[0], OLD_ACTIONS[1], Action(code=5, name="chest")),
        (OLD_ACTIONS[0], OLD_ACTIONS[1], Action(code=1, name="sell"), Action(code=5, name="x")),
    ],
    ids=["missing-code", "changed-meaning"],
)
def test_migration_refuses_an_old_action_the_new_catalog_lacks(
    tmp_path: Path, actions: tuple[Action, ...]
) -> None:
    # Given
    _ = _old_checkpoint(tmp_path / "old")
    catalog = Catalog(map_sha256="map", units=UNITS, actions=actions)

    # When / Then
    with pytest.raises(ProtocolError):
        _ = migrate_checkpoint(tmp_path / "old", tmp_path / "new", catalog)
    assert not (tmp_path / "new").exists()
