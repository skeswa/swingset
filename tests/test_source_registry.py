import subprocess
import sys

import pytest

from swingset.sources import get_page_kind, sources
from swingset.sources.registry import ADAPTERS, KIND_OWNERS


def test_kind_owners_match_every_adapters_page_kinds() -> None:
    registered = {item.name: item for item in sources()}
    owners = {kind: name for name, item in registered.items() for kind in item.page_kinds}
    assert KIND_OWNERS == owners
    assert list(registered) == list(ADAPTERS)
    for kind, owner in KIND_OWNERS.items():
        page = get_page_kind(kind)
        assert page.kind == kind
        assert page is registered[owner].page_kinds[kind]


def test_unknown_page_kind_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="unknown page kind"):
        get_page_kind("wsdc_registry.future_page")


def test_resolving_a_page_kind_imports_only_its_owner() -> None:
    script = (
        "import sys; from swingset.sources import get_page_kind; "
        "get_page_kind('eepro.round'); "
        "print(sorted(m for m in sys.modules if m.endswith('.adapter')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    ).stdout
    assert out.strip() == "['swingset.sources.eepro.adapter']"
