from waifu.brain.session import SessionClock


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_fresh_session_never_needs_a_reset_however_long_it_waits():
    clock = FakeClock()
    session = SessionClock(idle_minutes=10, max_turns=40, clock=clock)

    clock.now += 24 * 60 * 60

    assert not session.should_reset()


def test_session_continues_while_turns_keep_coming_within_the_idle_window():
    clock = FakeClock()
    session = SessionClock(idle_minutes=10, max_turns=40, clock=clock)

    session.record_turn()
    clock.now += 9 * 60
    session.record_turn()
    clock.now += 9 * 60

    assert not session.should_reset()


def test_session_resets_once_idle_longer_than_the_limit_since_the_last_turn():
    clock = FakeClock()
    session = SessionClock(idle_minutes=10, max_turns=40, clock=clock)
    session.record_turn()

    clock.now += 10 * 60
    assert not session.should_reset()

    clock.now += 0.001
    assert session.should_reset()


def test_session_resets_when_the_turn_cap_is_reached():
    session = SessionClock(idle_minutes=10, max_turns=3, clock=FakeClock())

    session.record_turn()
    session.record_turn()
    assert not session.should_reset()

    session.record_turn()
    assert session.should_reset()


def test_reset_starts_a_fresh_count_and_forgets_the_last_turn():
    clock = FakeClock()
    session = SessionClock(idle_minutes=10, max_turns=2, clock=clock)
    session.record_turn()
    session.record_turn()
    clock.now += 60 * 60

    session.reset()

    assert not session.should_reset()
    session.record_turn()
    assert not session.should_reset()


def test_default_clock_is_monotonic_time():
    session = SessionClock(idle_minutes=10, max_turns=40)

    session.record_turn()

    assert not session.should_reset()
