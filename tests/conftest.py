import pytest

from ord_ai.protocol import GameState, Status


@pytest.fixture
def state() -> GameState:
    return GameState(
        episode=19,
        sequence=0,
        round=1,
        gold=50,
        lumber=0,
        food=0,
        enemies=5,
        enemy_limit=100,
        death_count=20,
        story=1,
        bosses=0,
        status=Status.PLAYING,
        action_ok=1,
    )
