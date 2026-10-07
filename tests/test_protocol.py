import pytest
from pydantic import ValidationError

from ord_ai.protocol import Command, ProtocolError, Status, parse_snapshot


def encoded(records: tuple[str, ...]) -> str:
    return "\n".join(f'call Preload( "{record}" )' for record in records)


def test_snapshot_parses_complete_state_and_inventory() -> None:
    # Given
    text = encoded(
        (
            "ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1",
            "ORD_AI|1|UNIT|1747988531|3",
            "ORD_AI|1|END|19|2",
        )
    )

    # When
    state = parse_snapshot(text)

    # Then
    assert state is not None
    assert (state.episode, state.sequence, state.round, state.gold) == (19, 2, 3, 150)
    assert state.status == Status.PLAYING
    assert [(unit.raw_id, unit.count) for unit in state.units] == [(1747988531, 3)]


def test_incomplete_snapshot_is_not_an_observation() -> None:
    # Given
    text = encoded(("ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1",))

    # When
    state = parse_snapshot(text)

    # Then
    assert state is None


@pytest.mark.parametrize("ending", ["ORD_AI|1|END|20|2", "ORD_AI|1|END|19|3"])
def test_snapshot_rejects_mixed_episode_or_sequence(ending: str) -> None:
    # Given
    text = encoded(("ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1", ending))

    # When / Then
    with pytest.raises(ProtocolError):
        _ = parse_snapshot(text)


def test_snapshot_rejects_duplicate_unit_counts() -> None:
    # Given
    text = encoded(
        (
            "ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1",
            "ORD_AI|1|UNIT|1747988531|3",
            "ORD_AI|1|UNIT|1747988531|4",
            "ORD_AI|1|END|19|2",
        )
    )

    # When / Then
    with pytest.raises(ProtocolError):
        _ = parse_snapshot(text)


def test_command_encodes_the_control_reset_as_signed_action() -> None:
    # Given
    command = Command(episode=19, sequence=0, action=-1)

    # When
    script = command.preload_script()

    # Then
    assert '"ORD_AI|1|19|0|-1"' in script


@pytest.mark.parametrize(
    ("command", "name"),
    [
        (Command(episode=19, sequence=0, action=-1), "reset_19.txt"),
        (Command(episode=19, sequence=4, action=1003), "cmd_19_4.txt"),
    ],
)
def test_every_command_has_its_own_file(command: Command, name: str) -> None:
    # When / Then: the map polls exactly these names, one read per name.
    assert command.file_name == name


def test_snapshot_parses_tech_levels_beside_units() -> None:
    # Given
    text = encoded(
        (
            "ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1",
            "ORD_AI|1|UNIT|1747988531|3",
            "ORD_AI|1|TECH|1378889798|2",
            "ORD_AI|1|TECH|1378889785|0",
            "ORD_AI|1|END|19|2",
        )
    )

    # When
    state = parse_snapshot(text)

    # Then
    assert state is not None
    assert [(tech.raw_id, tech.level) for tech in state.techs] == [
        (1378889798, 2),
        (1378889785, 0),
    ]
    assert [(unit.raw_id, unit.count) for unit in state.units] == [(1747988531, 3)]


@pytest.mark.parametrize("record", ["ORD_AI|1|TECH|1378889798|2", "ORD_AI|1|GOLD|1|2"])
def test_snapshot_rejects_duplicate_tech_or_unknown_records(record: str) -> None:
    # Given
    text = encoded(
        (
            "ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1",
            "ORD_AI|1|TECH|1378889798|2",
            record,
            "ORD_AI|1|END|19|2",
        )
    )

    # When / Then
    with pytest.raises(ProtocolError):
        _ = parse_snapshot(text)


def test_snapshot_parses_the_extra_tech_rows_of_schema_3_masks() -> None:
    # Given: research levels and unlock flags beside the seven schema-2 rows.
    codes = ("R00F", "R009", "R01M", "R00G", "R01S", "R002", "R032", "Rhgb")
    rows = tuple(
        f"ORD_AI|1|TECH|{int.from_bytes(code.encode(), 'big')}|{index}"
        for index, code in enumerate(codes)
    )
    text = encoded(("ORD_AI|1|STATE|19|2|3|150|4|2|20|100|18|2|1|1|1", *rows, "ORD_AI|1|END|19|2"))

    # When
    state = parse_snapshot(text)

    # Then
    assert state is not None
    assert [tech.level for tech in state.techs] == list(range(len(codes)))


def test_the_exit_control_action_is_the_only_value_below_reset() -> None:
    assert Command(episode=1, sequence=5, action=-2).file_name == "cmd_1_5.txt"
    with pytest.raises(ValidationError):
        _ = Command(episode=1, sequence=5, action=-3)
