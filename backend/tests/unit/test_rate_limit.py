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
