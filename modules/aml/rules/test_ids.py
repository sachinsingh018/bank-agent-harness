from modules.aml.rules.ids import make_alert_id


def test_format_and_determinism() -> None:
    a = make_alert_id("v1", "fan-out", [3, 1, 2])
    assert a == make_alert_id("v1", "fan-out", [1, 2, 3])
    assert a.startswith("AL-")
    assert len(a) == 15
    assert all(c in "0123456789abcdef" for c in a[3:])


def test_inputs_change_the_id() -> None:
    base = make_alert_id("v1", "fan-out", [1, 2])
    assert base != make_alert_id("v2", "fan-out", [1, 2])
    assert base != make_alert_id("v1", "fan-in", [1, 2])
    assert base != make_alert_id("v1", "fan-out", [1, 3])


def test_separator_prevents_collisions() -> None:
    assert make_alert_id("v1", "fan-out", [12]) != make_alert_id("v1", "fan-out", [1, 2])
