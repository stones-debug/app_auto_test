from uuid import UUID

from scripts.backfill_app_profiles import normalize_nodes


def test_normalize_nodes_is_stable_and_preserves_valid_unique_keys():
    existing = "11111111-1111-4111-8111-111111111111"
    nodes = [{"key": existing, "action": "click"}, {"action": "wait"}]

    first, changed = normalize_nodes(7, "step", nodes)
    second, changed_again = normalize_nodes(7, "step", first)

    assert changed is True
    assert changed_again is False
    assert first == second
    assert first[0]["key"] == existing
    assert UUID(first[1]["key"])


def test_normalize_nodes_replaces_duplicate_keys_without_mutating_source():
    duplicate = "22222222-2222-4222-8222-222222222222"
    source = [{"key": duplicate}, {"key": duplicate}]

    normalized, changed = normalize_nodes(9, "assertion", source)

    assert changed is True
    assert source[0]["key"] == duplicate
    assert normalized[0]["key"] != normalized[1]["key"]
