"""A small on-policy, masked advantage actor-critic with a behavior-cloning step."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never

import torch
from torch import Tensor, nn
from torch.distributions import Categorical

from ord_ai.protocol import ProtocolError

HIDDEN_WIDTH: Final = 128
MASKED_LOGIT: Final = -1e9
DISCOUNT: Final = 0.99
VALUE_WEIGHT: Final = 0.5
ENTROPY_WEIGHT: Final = 0.01
GRADIENT_LIMIT: Final = 0.5
ADVANTAGE_EPSILON: Final = 1e-8
GAE_LAMBDA: Final = 0.95
CLIP_RANGE: Final = 0.2


class DeviceChoice(StrEnum):
    """Supported computation backends."""

    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


def select_device(choice: DeviceChoice) -> str:
    """Use CUDA when requested and actually available."""
    match choice:
        case DeviceChoice.AUTO:
            return "cuda" if torch.cuda.is_available() else "cpu"
        case DeviceChoice.CPU:
            return "cpu"
        case DeviceChoice.CUDA:
            if not torch.cuda.is_available():
                raise ProtocolError(detail="CUDA was requested but PyTorch cannot use this GPU")
            return "cuda"
    assert_never(choice)


class ActorCritic(nn.Module):
    """Trainable network; layer parameters intentionally change during learning."""

    def __init__(self, input_size: int, action_count: int) -> None:
        """Allocate the shared encoder and policy/value heads."""
        super().__init__()
        self.input_layer: nn.Linear = nn.Linear(input_size, HIDDEN_WIDTH)
        self.hidden_layer: nn.Linear = nn.Linear(HIDDEN_WIDTH, HIDDEN_WIDTH)
        self.policy_head: nn.Linear = nn.Linear(HIDDEN_WIDTH, action_count)
        self.value_head: nn.Linear = nn.Linear(HIDDEN_WIDTH, 1)

    def evaluate(self, observations: Tensor) -> tuple[Tensor, Tensor]:
        """Return policy logits and scalar value estimates for a batch."""
        hidden = torch.tanh(self.input_layer.forward(observations))
        hidden = torch.tanh(self.hidden_layer.forward(hidden))
        return self.policy_head.forward(hidden), self.value_head.forward(hidden).squeeze(-1)


def discounted_returns(
    rewards: Sequence[float],
    terminals: Sequence[bool],
    bootstrap: float,
    discount: float = DISCOUNT,
) -> list[float]:
    """Discount rewards backwards, restarting at terminals and bootstrapping the tail."""
    running_return = bootstrap
    returns: list[float] = []
    for reward, terminal in zip(reversed(rewards), reversed(terminals), strict=True):
        running_return = reward + (0.0 if terminal else discount * running_return)
        returns.append(running_return)
    returns.reverse()
    return returns


def generalized_advantages(
    rewards: Tensor,
    values: Tensor,
    terminals: Tensor,
    bootstrap: Tensor,
    discount: float = DISCOUNT,
) -> tuple[Tensor, Tensor]:
    """GAE advantages and value targets for ``[time, env]`` rollouts.

    ``terminals[t]`` marks that the step at ``t`` ended its episode, so nothing flows back
    across it; ``bootstrap`` is the value of each environment's state after the last step.
    """
    advantages = torch.zeros_like(rewards)
    running = torch.zeros_like(bootstrap)
    following = bootstrap
    for step in reversed(range(rewards.shape[0])):
        alive = 1.0 - terminals[step].float()
        delta = rewards[step] + discount * following * alive - values[step]
        running = delta + discount * GAE_LAMBDA * alive * running
        advantages[step] = running
        following = values[step]
    return advantages, advantages + values


@dataclass(frozen=True, slots=True)
class Rollout:
    """A flattened on-policy batch with the sampling policy's log-probabilities."""

    observations: Tensor
    masks: Tensor
    actions: Tensor
    log_probabilities: Tensor
    advantages: Tensor
    returns: Tensor


@dataclass(frozen=True, slots=True)
class Experience:
    """One on-policy action and the real environment result."""

    observation: tuple[float, ...]
    mask: tuple[bool, ...]
    action: int
    reward: float
    terminal: bool


@dataclass(frozen=True, slots=True)
class Demonstration:
    """One teacher-chosen action and the return observed after it."""

    observation: tuple[float, ...]
    mask: tuple[bool, ...]
    action: int
    target_return: float


@dataclass(frozen=True, slots=True)
class DemoBatch:
    """Demonstrations as tensors, one row per decision."""

    observations: Tensor
    masks: Tensor
    actions: Tensor
    returns: Tensor
    weights: Tensor | None = None  # per-row scale of the policy loss; None weighs rows alike


@dataclass(frozen=True, slots=True)
class Losses:
    """Metrics from one gradient update."""

    policy: float
    value: float
    entropy: float


class Learner:
    """Own mutable network/optimizer state for one policy vocabulary."""

    def __init__(
        self,
        model: ActorCritic,
        device: str,
        learning_rate: float = 0.0003,
        entropy_weight: float = ENTROPY_WEIGHT,
        discount: float = DISCOUNT,
    ) -> None:
        """Place the network and initialize its Adam optimizer."""
        self.model: ActorCritic = model.to(device)
        self.device: str = device
        self.entropy_weight: float = entropy_weight
        self.discount: float = discount
        self.optimizer: torch.optim.Optimizer = torch.optim.Adam(
            model.parameters(), lr=learning_rate
        )

    def choose(self, observation: tuple[float, ...], mask: tuple[bool, ...]) -> int:
        """Sample only actions permitted by the resource mask."""
        if not any(mask):
            raise ProtocolError(detail="A policy observation must permit at least one action")
        with torch.no_grad():
            tensor = torch.tensor([observation], dtype=torch.float32, device=self.device)
            logits, _ = self.model.evaluate(tensor)
            allowed = torch.tensor([mask], dtype=torch.bool, device=self.device)
            distribution = Categorical(logits=logits.masked_fill(~allowed, MASKED_LOGIT))
            return int(distribution.sample().item())

    def best(self, observation: tuple[float, ...], mask: tuple[bool, ...]) -> int:
        """Return the most likely permitted action (no sampling)."""
        if not any(mask):
            raise ProtocolError(detail="A policy observation must permit at least one action")
        with torch.no_grad():
            tensor = torch.tensor([observation], dtype=torch.float32, device=self.device)
            logits, _ = self.model.evaluate(tensor)
            allowed = torch.tensor([mask], dtype=torch.bool, device=self.device)
            return int(logits.masked_fill(~allowed, MASKED_LOGIT).argmax(dim=-1).item())

    def value(self, observation: tuple[float, ...]) -> float:
        """Bootstrap a nonterminal rollout using the current critic."""
        with torch.no_grad():
            tensor = torch.tensor([observation], dtype=torch.float32, device=self.device)
            _, value = self.model.evaluate(tensor)
            return float(value.item())

    def update(self, experiences: list[Experience], bootstrap: float) -> Losses:
        """Compute discounted returns and perform one A2C gradient update."""
        if not experiences:
            raise ProtocolError(detail="Cannot update from an empty rollout")
        returns = discounted_returns(
            [item.reward for item in experiences],
            [item.terminal for item in experiences],
            bootstrap,
            self.discount,
        )
        targets = torch.tensor(returns, dtype=torch.float32, device=self.device)
        selected, values, entropy = self._evaluate_batch(experiences)
        advantage = targets - values
        policy_advantage = advantage.detach()
        if len(experiences) > 1:
            # Keeps the policy step independent of the reward scale; the critic still
            # regresses onto the raw returns.
            policy_advantage = (policy_advantage - policy_advantage.mean()) / (
                policy_advantage.std() + ADVANTAGE_EPSILON
            )
        policy_loss = torch.neg(torch.mean(torch.mul(selected, policy_advantage)))
        value_loss = advantage.square().mean()
        self._step(policy_loss + VALUE_WEIGHT * value_loss - self.entropy_weight * entropy)
        return Losses(
            policy=float(policy_loss.detach().item()),
            value=float(value_loss.detach().item()),
            entropy=float(entropy.detach().item()),
        )

    def ppo_update(self, rollout: Rollout, epochs: int, minibatch: int) -> Losses:
        """Clipped-surrogate PPO epochs over one rollout; returns the last minibatch's losses."""
        count = rollout.actions.shape[0]
        if count == 0:
            raise ProtocolError(detail="Cannot update from an empty rollout")
        advantages = rollout.advantages
        advantages = (advantages - advantages.mean()) / (advantages.std() + ADVANTAGE_EPSILON)
        losses = Losses(policy=0.0, value=0.0, entropy=0.0)
        for _ in range(epochs):
            order = torch.randperm(count, device=self.device)
            for start in range(0, count, minibatch):
                rows = order[start : start + minibatch]
                logits, values = self.model.evaluate(rollout.observations[rows])
                masked = logits.masked_fill(~rollout.masks[rows], MASKED_LOGIT)
                log_probabilities = torch.log_softmax(masked, dim=-1)
                selected = log_probabilities.gather(1, rollout.actions[rows].unsqueeze(1))
                ratio = torch.exp(selected.squeeze(1) - rollout.log_probabilities[rows])
                clipped = torch.clamp(ratio, 1.0 - CLIP_RANGE, 1.0 + CLIP_RANGE)
                advantage = advantages[rows]
                policy_loss = torch.neg(
                    torch.minimum(ratio * advantage, clipped * advantage).mean()
                )
                value_loss = (rollout.returns[rows] - values).square().mean()
                entropy = torch.neg(
                    torch.sum(log_probabilities.exp() * log_probabilities, dim=-1)
                ).mean()
                self._step(policy_loss + VALUE_WEIGHT * value_loss - self.entropy_weight * entropy)
                losses = Losses(
                    policy=float(policy_loss.detach().item()),
                    value=float(value_loss.detach().item()),
                    entropy=float(entropy.detach().item()),
                )
        return losses

    def imitate(self, demonstrations: Sequence[Demonstration]) -> Losses:
        """Perform one behavior-cloning step toward demonstrated actions and returns."""
        if not demonstrations:
            raise ProtocolError(detail="Cannot imitate an empty batch of demonstrations")
        targets = torch.tensor(
            [item.target_return for item in demonstrations],
            dtype=torch.float32,
            device=self.device,
        )
        selected, values, entropy = self._evaluate_batch(demonstrations)
        policy_loss = torch.neg(selected.mean())
        value_loss = (targets - values).square().mean()
        self._step(policy_loss + VALUE_WEIGHT * value_loss)
        return Losses(
            policy=float(policy_loss.detach().item()),
            value=float(value_loss.detach().item()),
            entropy=float(entropy.detach().item()),
        )

    def imitate_batch(self, batch: DemoBatch, smoothing: float = 0.0) -> Losses:
        """Behavior-cloning step like :meth:`imitate` for demonstrations already in tensors.

        ``smoothing`` moves that share of the target onto every allowed action, so actions
        the teacher never takes keep a probability reinforcement learning can explore.
        ``batch.weights`` (one per row) scale each row's share of the policy loss.
        """
        observations, masks, actions = batch.observations, batch.masks, batch.actions
        target_returns, weights = batch.returns, batch.weights
        logits, values = self.model.evaluate(observations.to(self.device))
        allowed = masks.to(self.device)
        log_probabilities = torch.log_softmax(logits.masked_fill(~allowed, MASKED_LOGIT), dim=-1)
        selected = log_probabilities.gather(1, actions.to(self.device).unsqueeze(1)).squeeze(1)
        entropy = torch.neg(torch.sum(log_probabilities.exp() * log_probabilities, dim=-1)).mean()
        spread = torch.sum(log_probabilities * allowed, dim=-1) / allowed.sum(dim=-1)
        rows = (1.0 - smoothing) * selected + smoothing * spread
        scale = torch.ones_like(rows) if weights is None else weights.to(self.device)
        policy_loss = torch.neg((rows * scale).sum() / scale.sum())
        value_loss = (target_returns.to(self.device) - values).square().mean()
        self._step(policy_loss + VALUE_WEIGHT * value_loss)
        return Losses(
            policy=float(policy_loss.detach().item()),
            value=float(value_loss.detach().item()),
            entropy=float(entropy.detach().item()),
        )

    def _evaluate_batch(
        self, batch: Sequence[Experience | Demonstration]
    ) -> tuple[Tensor, Tensor, Tensor]:
        """Return taken-action masked log-probabilities, values and mean policy entropy."""
        observations = torch.tensor(
            [item.observation for item in batch], dtype=torch.float32, device=self.device
        )
        masks = torch.tensor([item.mask for item in batch], dtype=torch.bool, device=self.device)
        actions = torch.tensor(
            [item.action for item in batch], dtype=torch.long, device=self.device
        )
        logits, values = self.model.evaluate(observations)
        log_probabilities = torch.log_softmax(logits.masked_fill(~masks, MASKED_LOGIT), dim=-1)
        selected = log_probabilities.gather(1, actions.unsqueeze(1)).squeeze(1)
        entropy = torch.neg(torch.sum(log_probabilities.exp() * log_probabilities, dim=-1)).mean()
        return selected, values, entropy

    def _step(self, loss: Tensor) -> None:
        """Descend ``loss`` with one gradient-clipped Adam step."""
        self.optimizer.zero_grad(set_to_none=True)
        torch.autograd.backward(loss)
        _ = nn.utils.clip_grad_norm_(self.model.parameters(), GRADIENT_LIMIT)
        _ = self.optimizer.step()
