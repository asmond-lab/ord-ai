"""Portable policy weights tied to the exact observation/action vocabulary."""

from pathlib import Path
from typing import ClassVar

import torch
from pydantic import BaseModel, ConfigDict
from safetensors.torch import load, save_model

from ord_ai.boss import BOSS_SIZE
from ord_ai.catalog import Catalog
from ord_ai.learning import ActorCritic
from ord_ai.observation import input_size
from ord_ai.protocol import ProtocolError
from ord_ai.treasure import TREASURE_SIZE

# Feature tails added after a checkpoint was trained, newest last: a pre-chest checkpoint
# lacks the chest and boss columns, a pre-boss one only the boss columns.
MISSING_TAILS: frozenset[int] = frozenset({TREASURE_SIZE + BOSS_SIZE, BOSS_SIZE})


class Migration(BaseModel):
    """The checkpoint a migrated one was converted from."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    catalog_fingerprint: str
    path: str


class Metadata(BaseModel):
    """Architecture and provenance needed to interpret saved policy weights."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = 1
    algorithm: str = "masked-a2c"
    catalog_fingerprint: str
    steps: int
    input_size: int
    action_count: int
    migrated_from: Migration | None = None


def save_checkpoint(directory: Path, model: ActorCritic, metadata: Metadata) -> None:
    """Save weights without pickle and atomically replace each checkpoint file."""
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "policy.tmp.safetensors"
    save_model(model, str(temporary))
    _ = temporary.replace(directory / "policy.safetensors")
    temporary = directory / "metadata.tmp.json"
    _ = temporary.write_text(metadata.model_dump_json(indent=2, exclude_none=True), "utf-8")
    _ = temporary.replace(directory / "metadata.json")


def load_checkpoint(directory: Path, catalog: Catalog, device: str) -> ActorCritic:
    """Refuse to run a policy against a different map or action ordering."""
    metadata = Metadata.model_validate_json((directory / "metadata.json").read_text("utf-8"))
    expected_size = input_size(catalog)
    if (
        metadata.catalog_fingerprint != catalog.fingerprint
        or metadata.input_size != expected_size
        or metadata.action_count != len(catalog.actions)
        or metadata.schema_version != 1
        or metadata.algorithm != "masked-a2c"
    ):
        raise ProtocolError(detail="Checkpoint and map catalog are incompatible")
    model = ActorCritic(expected_size, len(catalog.actions)).to(device)
    weights = load((directory / "policy.safetensors").read_bytes())
    _ = model.load_state_dict(weights, strict=True)
    return model


def migrate_checkpoint(source: Path, destination: Path, catalog: Catalog) -> Metadata:
    """Convert a checkpoint trained before newer actions or input features to ``catalog``.

    Old actions keep their weights, matched by wire code; each new action gets a zero weight
    row and the old actions' mean bias. The newer features (hidden chests, round boss) are
    the input tail (``ord_ai.observation.features``) and get zero input columns, so the
    migrated policy reproduces the old logits and value while every new feature is 0.
    """
    if (destination / "policy.safetensors").exists():
        raise ProtocolError(detail=f"{destination} already holds a checkpoint")
    metadata = Metadata.model_validate_json((source / "metadata.json").read_text("utf-8"))
    old = Catalog.model_validate_json((source / "catalog.json").read_text("utf-8"))
    # The input the old model saw: the current layout of its catalog minus a newer tail.
    old_size = metadata.input_size
    missing = input_size(old) - old_size
    if (
        metadata.catalog_fingerprint != old.fingerprint
        or missing not in MISSING_TAILS
        or metadata.action_count != len(old.actions)
        or metadata.schema_version != 1
        or metadata.algorithm != "masked-a2c"
    ):
        raise ProtocolError(detail=f"{source} is not an older checkpoint of its catalog.json")
    if old.units != catalog.units or input_size(catalog) != old_size + missing:
        raise ProtocolError(detail="The unit-count features differ between the two catalogs")
    new_index = {action.code: index for index, action in enumerate(catalog.actions)}
    if len(new_index) != len(catalog.actions) or len({a.code for a in old.actions}) != len(
        old.actions
    ):
        raise ProtocolError(detail="A catalog repeats an action wire code")
    for action in old.actions:
        index = new_index.get(action.code)
        if index is None or catalog.actions[index] != action:
            raise ProtocolError(detail=f"Action {action.code} ({action.name}) changed or vanished")

    weights = load((source / "policy.safetensors").read_bytes())
    # ActorCritic has parameters only (no buffers), so these are all of its tensors.
    expected = ActorCritic(old_size, len(old.actions)).named_parameters()
    shapes = {name: tuple(tensor.shape) for name, tensor in weights.items()}
    if shapes != {name: tuple(parameter.shape) for name, parameter in expected}:
        raise ProtocolError(detail=f"Unexpected tensors in {source}: {shapes}")
    migrated = dict(weights)
    first = weights["input_layer.weight"]
    migrated["input_layer.weight"] = torch.cat(
        (first, first.new_zeros(first.shape[0], missing)), dim=1
    )
    head, bias = weights["policy_head.weight"], weights["policy_head.bias"]
    rows = [new_index[action.code] for action in old.actions]
    new_head = head.new_zeros(len(catalog.actions), head.shape[1])
    new_head[rows] = head
    new_bias = torch.full((len(catalog.actions),), bias.mean().item(), dtype=bias.dtype)
    new_bias[rows] = bias
    migrated["policy_head.weight"], migrated["policy_head.bias"] = new_head, new_bias

    model = ActorCritic(input_size(catalog), len(catalog.actions))
    _ = model.load_state_dict(migrated, strict=True)
    result = Metadata(
        algorithm=metadata.algorithm,
        catalog_fingerprint=catalog.fingerprint,
        steps=metadata.steps,
        input_size=input_size(catalog),
        action_count=len(catalog.actions),
        migrated_from=Migration(
            catalog_fingerprint=metadata.catalog_fingerprint, path=str(source.resolve())
        ),
    )
    save_checkpoint(destination, model, result)
    _ = (destination / "catalog.json").write_text(catalog.model_dump_json(indent=2), "utf-8")
    return result
