import pytest

from app.platform.auth import rate_limit
from app.platform.auth.rate_limit import LoginRateLimiter


def test_blocks_after_max_failures_and_resets() -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        assert not limiter.is_blocked("k")
        limiter.register_failure("k")
    assert limiter.is_blocked("k")
    limiter.reset("k")
    assert not limiter.is_blocked("k")


def test_keys_are_independent() -> None:
    limiter = LoginRateLimiter(max_attempts=1, window_seconds=60)
    limiter.register_failure("a")
    assert limiter.is_blocked("a")
    assert not limiter.is_blocked("b")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(rate_limit.time, "monotonic", fake)
    return fake


def test_the_window_and_the_limit_are_unchanged(clock: FakeClock) -> None:
    """Blocked at max_attempts failures within the window; a failure older than the window no longer counts."""
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for _ in range(2):
        limiter.register_failure("ip|a@x")
    clock.now += 30
    limiter.register_failure("ip|a@x")
    assert limiter.is_blocked("ip|a@x")
    clock.now += 31  # the first two are now 61 s old
    assert not limiter.is_blocked("ip|a@x")
    clock.now += 30  # the third is 61 s old too
    assert not limiter.is_blocked("ip|a@x")


def test_asking_about_a_key_does_not_store_it(clock: FakeClock) -> None:
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for n in range(100):
        assert not limiter.is_blocked(f"ip{n}|a@x")
    assert len(limiter) == 0


def test_expired_keys_are_dropped_even_if_never_asked_about_again(clock: FakeClock) -> None:
    """Thousands of IP + email pairs fail once and never come back: memory goes back to the recent ones."""
    limiter = LoginRateLimiter(max_attempts=3, window_seconds=60)
    for n in range(5000):
        limiter.register_failure(f"10.0.{n // 256}.{n % 256}|user{n}@x")
    assert len(limiter) == 5000
    clock.now += 61
    limiter.register_failure("10.9.9.9|other@x")  # any later failure sweeps what expired
    assert len(limiter) == 1
    assert limiter.is_blocked("10.9.9.9|other@x") is False


def test_a_key_still_inside_the_window_survives_the_sweep(clock: FakeClock) -> None:
    limiter = LoginRateLimiter(max_attempts=2, window_seconds=60)
    limiter.register_failure("old|a@x")
    clock.now += 40
    limiter.register_failure("recent|b@x")
    clock.now += 21  # "old" expired, "recent" is 21 s old
    limiter.register_failure("recent|b@x")
    assert len(limiter) == 1
    assert limiter.is_blocked("recent|b@x")
