from swingset.build.builder import BuildInput
from swingset.build.schema import PRIMARY_KEYS, SCHEMAS
from swingset.publish.card import render_card


def test_card_lists_every_table_and_one_default() -> None:
    data = BuildInput({}, SCHEMAS, PRIMARY_KEYS, {}, {}, "bundle")
    card = render_card(data).decode()
    assert card.count("config_name:") == len(SCHEMAS)
    assert card.count("default: true") == 1
    assert "ODC-By 1.0" in card
    assert "five-second" in card
