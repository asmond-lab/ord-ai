from pathlib import Path

import pytest
import torch

from ord_ai.catalog import Action, Catalog
from ord_ai.checkpoint import Metadata, load_checkpoint, save_checkpoint
from ord_ai.learning import (
    DISCOUNT,
    GAE_LAMBDA,
    MASKED_LOGIT,
    ActorCritic,
    DemoBatch,
    Demonstration,
    Experience,
    Learner,
    Losses,
    Rollout,
    discounted_returns,
    generalized_advantages,
)
from ord_ai.observation import SCALAR_COUNT, input_size
from ord_ai.protocol import ProtocolError


def test_policy_sampling_never_selects_a_masked_action() -> None:
    # Given
    learner = Learner(ActorCritic(2, 2), "cpu")

    # When
    choices = [learner.choose((1.0, 0.0), (True, False)) for _ in range(8)]

    # Then
    assert choices == [0] * 8


def test_gradient_update_changes_the_rewarded_action_probability() -> None:
    # Given
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 2), "cpu", learning_rate=0.01)
    observation = torch.tensor([[1.0, 0.0]])
    with torch.no_grad():
        before, _ = learner.model.evaluate(observation)
        old_probability = float(torch.softmax(before, dim=-1)[0, 1].item())
    rollout = [Experience((1.0, 0.0), (True, True), 1, 10.0, terminal=True)]

    # When
    _ = learner.update(rollout, 0.0)

    # Then
    with torch.no_grad():
        after, _ = learner.model.evaluate(observation)
        probability = float(torch.softmax(after, dim=-1)[0, 1].item())
    assert probability > old_probability


def _losses_for_reward_scale(scale: float) -> Losses:
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 2), "cpu")
    rollout = [
        Experience((1.0, 0.0), (True, True), 0, scale, terminal=True),
        Experience((1.0, 0.0), (True, True), 1, 0.0, terminal=True),
    ]
    return learner.update(rollout, 0.0)


def test_policy_loss_ignores_reward_scale_when_advantages_are_normalized() -> None:
    # Given / When: identical networks see the same rollout at two reward scales.
    small, large = _losses_for_reward_scale(1.0), _losses_for_reward_scale(100.0)

    # Then
    assert large.policy == pytest.approx(small.policy)


def test_value_loss_keeps_the_raw_reward_scale() -> None:
    # Given / When: identical networks see the same rollout at two reward scales.
    small, large = _losses_for_reward_scale(1.0), _losses_for_reward_scale(100.0)

    # Then
    assert large.value > 1000 * small.value


def test_discounted_returns_cut_at_terminals_and_bootstrap_the_tail() -> None:
    # Given: the second step ends an episode; the third starts the next one.
    rewards = [1.0, 4.0, 3.0]
    terminals = [False, True, False]

    # When
    returns = discounted_returns(rewards, terminals, bootstrap=4.0, discount=0.5)

    # Then
    assert returns == [3.0, 4.0, 5.0]


def test_imitation_raises_the_demonstrated_action_probability() -> None:
    # Given
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 2), "cpu", learning_rate=0.01)
    observation = torch.tensor([[1.0, 0.0]])
    with torch.no_grad():
        before, _ = learner.model.evaluate(observation)
        old_probability = float(torch.softmax(before, dim=-1)[0, 1].item())
    demonstrations = [Demonstration((1.0, 0.0), (True, True), 1, 0.0)]

    # When
    _ = learner.imitate(demonstrations)

    # Then
    with torch.no_grad():
        after, _ = learner.model.evaluate(observation)
        probability = float(torch.softmax(after, dim=-1)[0, 1].item())
    assert probability > old_probability


def test_imitation_keeps_a_masked_action_improbable() -> None:
    # Given
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 3), "cpu", learning_rate=0.01)
    mask = (True, True, False)
    demonstrations = [Demonstration((1.0, 0.0), mask, 1, 0.0)]

    # When
    for _ in range(20):
        _ = learner.imitate(demonstrations)

    # Then: evaluated with the same masking that choose samples from.
    with torch.no_grad():
        logits, _ = learner.model.evaluate(torch.tensor([[1.0, 0.0]]))
        masked = logits.masked_fill(~torch.tensor([mask]), MASKED_LOGIT)
        probability = float(torch.softmax(masked, dim=-1)[0, 2].item())
    assert probability < 1e-6


def test_imitation_rejects_an_empty_batch() -> None:
    # Given
    learner = Learner(ActorCritic(2, 2), "cpu")

    # When / Then
    with pytest.raises(ProtocolError):
        _ = learner.imitate([])


def test_checkpoint_restores_exact_policy_outputs(tmp_path: Path) -> None:
    # Given
    catalog = Catalog(map_sha256="test", units=(), actions=(Action(code=0, name="wait"),))
    model = ActorCritic(input_size(catalog), 1)
    metadata = Metadata(
        catalog_fingerprint=catalog.fingerprint,
        steps=1,
        input_size=input_size(catalog),
        action_count=1,
    )
    save_checkpoint(tmp_path, model, metadata)
    observation = torch.ones(1, input_size(catalog))
    expected, _ = model.evaluate(observation)

    # When
    loaded = load_checkpoint(tmp_path, catalog, "cpu")

    # Then
    actual, _ = loaded.evaluate(observation)
    assert torch.equal(expected, actual)


def test_checkpoint_rejects_a_different_map_catalog(tmp_path: Path) -> None:
    # Given
    catalog = Catalog(map_sha256="different", units=(), actions=(Action(code=0, name="wait"),))
    model = ActorCritic(SCALAR_COUNT, 1)
    save_checkpoint(
        tmp_path,
        model,
        Metadata(
            catalog_fingerprint="original",
            steps=1,
            input_size=SCALAR_COUNT,
            action_count=1,
        ),
    )

    # When / Then
    with pytest.raises(ProtocolError):
        _ = load_checkpoint(tmp_path, catalog, "cpu")


def _untaught_probability(smoothing: float) -> float:
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 3), "cpu", learning_rate=0.05)
    observations = torch.tensor([[1.0, 0.0]] * 8)
    masks = torch.tensor([[True, True, True]] * 8)
    actions = torch.zeros(8, dtype=torch.long)
    returns = torch.zeros(8)
    for _ in range(300):
        _ = learner.imitate_batch(DemoBatch(observations, masks, actions, returns), smoothing)
    with torch.no_grad():
        logits, _ = learner.model.evaluate(observations[:1])
        return float(torch.softmax(logits, dim=-1)[0, 2].item())


def test_smoothed_imitation_keeps_untaught_allowed_actions_explorable() -> None:
    # Given: the teacher always takes action 0 of three allowed actions.

    # When
    plain = _untaught_probability(0.0)
    smoothed = _untaught_probability(0.3)

    # Then
    assert plain < 0.01
    assert smoothed > 0.05


def _rare_probability(weights: torch.Tensor | None) -> float:
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 3), "cpu", learning_rate=0.05)
    observations = torch.tensor([[1.0, 0.0]] * 8)
    masks = torch.tensor([[True, True, True]] * 8)
    actions = torch.tensor([0, 0, 0, 0, 0, 0, 0, 1])
    batch = DemoBatch(observations, masks, actions, torch.zeros(8), weights)
    for _ in range(300):
        _ = learner.imitate_batch(batch)
    with torch.no_grad():
        logits, _ = learner.model.evaluate(observations[:1])
        return float(torch.softmax(logits, dim=-1)[0, 1].item())


def test_row_weights_shift_imitation_toward_the_rare_action() -> None:
    # Given: the same state shows the common action 0 seven times and action 1 once.
    down_weighted = torch.tensor([0.1] * 7 + [1.0])

    # When
    plain = _rare_probability(None)
    weighted = _rare_probability(down_weighted)

    # Then: unweighted cloning matches the 1/8 frequency; down-weighting action 0 favors 1.
    assert plain == pytest.approx(1 / 8, abs=0.03)
    assert weighted > 3 * plain


def test_generalized_advantages_stop_at_a_terminal_and_bootstrap_open_games() -> None:
    # Given: env 0 ends at step 1; env 1 keeps playing into a state worth 5.
    rewards = torch.tensor([[1.0, 1.0], [1.0, 1.0]])
    values = torch.zeros(2, 2)
    terminals = torch.tensor([[False, False], [True, False]])

    # When
    advantages, returns = generalized_advantages(
        rewards, values, terminals, torch.tensor([9.0, 5.0])
    )

    # Then
    ended_last = 1.0
    open_last = 1.0 + DISCOUNT * 5.0
    step_zero = (
        1.0 + DISCOUNT * GAE_LAMBDA * ended_last,
        1.0 + DISCOUNT * GAE_LAMBDA * open_last,
    )
    assert torch.allclose(advantages[1], torch.tensor([ended_last, open_last]))
    assert torch.allclose(advantages[0], torch.tensor(step_zero))
    assert torch.equal(returns, advantages)


def test_generalized_advantages_use_the_given_discount() -> None:
    # Given: one open game, a zero critic, and a bootstrap worth 10.
    rewards = torch.tensor([[0.0]])
    values = torch.zeros(1, 1)
    terminals = torch.tensor([[False]])

    # When
    advantages, _ = generalized_advantages(
        rewards, values, terminals, torch.tensor([10.0]), discount=0.5
    )

    # Then
    assert torch.allclose(advantages, torch.tensor([[5.0]]))


def test_ppo_update_raises_the_probability_of_the_advantaged_action() -> None:
    # Given: action 1 did better than action 0 from the same state.
    torch.set_rng_state(torch.Generator().manual_seed(7).get_state())
    learner = Learner(ActorCritic(2, 2), "cpu", learning_rate=0.01)
    observations = torch.tensor([[1.0, 0.0]] * 4)
    with torch.no_grad():
        logits, _ = learner.model.evaluate(observations[:1])
        before = torch.log_softmax(logits, dim=-1)[0]
    actions = torch.tensor([0, 1, 0, 1])
    rollout = Rollout(
        observations,
        torch.ones(4, 2, dtype=torch.bool),
        actions,
        before[actions],
        torch.tensor([-1.0, 1.0, -1.0, 1.0]),
        torch.zeros(4),
    )

    # When
    _ = learner.ppo_update(rollout, epochs=4, minibatch=2)

    # Then
    with torch.no_grad():
        logits, _ = learner.model.evaluate(observations[:1])
        after = torch.softmax(logits, dim=-1)[0, 1].item()
    assert after > float(before.exp()[1].item())
