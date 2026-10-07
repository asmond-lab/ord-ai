"""Synthetic bandit diagnostic; this is deliberately not a Warcraft simulator."""

from pathlib import Path
from typing import ClassVar

import torch
from pydantic import BaseModel, ConfigDict

from ord_ai.catalog import Action, Catalog
from ord_ai.checkpoint import Metadata, load_checkpoint, save_checkpoint
from ord_ai.learning import ActorCritic, DeviceChoice, Experience, Learner, select_device
from ord_ai.observation import SCALAR_COUNT


class SmokeResult(BaseModel):
    """Measured neural learning and persistence in an explicitly synthetic task."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    environment: str = "synthetic-two-action-bandit-not-warcraft"
    device: str
    probability_before: float
    probability_after: float
    checkpoint_equal: bool
    updates: int


def run_smoke(directory: Path, choice: DeviceChoice) -> SmokeResult:
    """Learn a rewarded action and prove its saved parameters load unchanged."""
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    device = select_device(choice)
    catalog = Catalog(
        map_sha256="synthetic-not-warcraft",
        units=(),
        actions=(Action(code=0, name="negative"), Action(code=1, name="positive")),
    )
    learner = Learner(ActorCritic(SCALAR_COUNT, 2), device, learning_rate=0.003)
    if device == "cuda":
        for generator in torch.cuda.default_generators:
            _ = generator.manual_seed(7)
    observation = (1.0,) + (0.0,) * (SCALAR_COUNT - 1)
    tensor = torch.tensor([observation], device=device)
    with torch.no_grad():
        logits, _ = learner.model.evaluate(tensor)
        before = float(torch.softmax(logits, dim=-1)[0, 1].item())
    updates = 48
    for _ in range(updates):
        rollout: list[Experience] = []
        for _ in range(16):
            action = learner.choose(observation, (True, True))
            rollout.append(
                Experience(
                    observation,
                    (True, True),
                    action,
                    1.0 if action == 1 else -1.0,
                    terminal=True,
                )
            )
        _ = learner.update(rollout, 0.0)
    with torch.no_grad():
        logits_after, _ = learner.model.evaluate(tensor)
        after = float(torch.softmax(logits_after, dim=-1)[0, 1].item())
    save_checkpoint(
        directory,
        learner.model,
        Metadata(
            catalog_fingerprint=catalog.fingerprint,
            steps=updates * 16,
            input_size=SCALAR_COUNT,
            action_count=2,
        ),
    )
    loaded = load_checkpoint(directory, catalog, device)
    with torch.no_grad():
        restored, _ = loaded.evaluate(tensor)
    return SmokeResult(
        device=device,
        probability_before=before,
        probability_after=after,
        checkpoint_equal=bool(torch.equal(logits_after, restored)),
        updates=updates,
    )
