"""Worker actor wiring tests."""

from cnb_worker.tasks import ping


def test_ping_actor_has_a_pure_diagnostic_function() -> None:
    assert ping.fn("ready") == {"status": "ready"}
