import logging
import random
import threading

import pytest

from waifu.avatar import interactions
from waifu.avatar.interactions import QUEUE_SIZE, WELCOME_BACK_GAP_S, InteractionHandler
from waifu.config import AvatarInteractionsConfig

LOGGER = "waifu.avatar.interactions"
WORKER_NAME = "avatar-interactions"
WAIT_S = 2.0  # an upper bound for events between threads, never a sleep
LINES = AvatarInteractionsConfig().lines


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Assistant:
    """Stands in for the Assistant the handler is bound to; records what it was asked to do."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.busy = False
        self.events: list[str] = []
        self.spoken: list[tuple[str, str]] = []
        self.speak_error: Exception | None = None
        self.listen_error: Exception | None = None
        self.speak_s = 0.0  # how long a line takes on the fake clock
        self.on_speak = lambda: None
        self.threads: list[threading.Thread] = []

    def speak(self, text: str, emotion: str) -> None:
        self.events.append(f"speak {emotion}")
        self.spoken.append((text, emotion))
        self.threads.append(threading.current_thread())
        self.on_speak()
        self.clock.now += self.speak_s
        if self.speak_error is not None:
            raise self.speak_error

    def listen(self) -> None:
        self.events.append("listen")
        if self.listen_error is not None:
            raise self.listen_error

    def is_busy(self) -> bool:
        return self.busy


def make_handler(
    *, rng: random.Random | None = None, **config: object
) -> tuple[InteractionHandler, Assistant, FakeClock]:
    clock = FakeClock()
    assistant = Assistant(clock)
    handler = InteractionHandler(
        AvatarInteractionsConfig.model_validate(config),
        speak=assistant.speak,
        listen=assistant.listen,
        is_busy=assistant.is_busy,
        clock=clock,
        rng=rng or random.Random(7),
    )
    return handler, assistant, clock


def react(handler: InteractionHandler, *kinds: str) -> None:
    """Queue `kinds`, then handle them in this thread, as the worker would."""
    for kind in kinds:
        handler.handle(kind)
    handler.drain()


def speak_times(assistant: Assistant, clock: FakeClock) -> list[float]:
    """When each line she says from now on starts, on the fake clock."""
    times: list[float] = []
    assistant.on_speak = lambda: times.append(clock.now)
    return times


def workers() -> int:
    return sum(thread.name == WORKER_NAME for thread in threading.enumerate())


# What each interaction does


def test_handle_only_queues_the_interaction():
    handler, assistant, _ = make_handler()

    handler.handle("head_pat")

    assert assistant.events == []
    handler.drain()
    assert assistant.events == ["speak happy"]


def test_a_head_pat_gets_a_happy_head_pat_line():
    handler, assistant, _ = make_handler()

    react(handler, "head_pat")

    [(line, emotion)] = assistant.spoken
    assert line in LINES["head_pat"]
    assert emotion == "happy"


def test_a_poke_on_the_body_gets_a_surprised_line():
    handler, assistant, _ = make_handler()

    react(handler, "body_touch")

    [(line, emotion)] = assistant.spoken
    assert line in LINES["body_touch"]
    assert emotion == "surprised"


def test_a_double_click_starts_listening_then_says_a_listen_line():
    handler, assistant, _ = make_handler()

    react(handler, "double_click")

    assert assistant.events == ["listen", "speak happy"]
    assert assistant.spoken[0][0] in LINES["listen"]


def test_coming_back_gets_a_happy_welcome():
    handler, assistant, _ = make_handler()

    react(handler, "welcome_back")

    [(line, emotion)] = assistant.spoken
    assert line in LINES["welcome_back"]
    assert emotion == "happy"


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("head_pat", {"enabled": False}),
        ("body_touch", {"enabled": False}),
        ("double_click", {"enabled": False}),
        ("welcome_back", {"enabled": False}),
        ("head_pat", {"head_pat": False}),
        ("body_touch", {"body_touch": False}),
        ("double_click", {"double_click_listen": False}),
        ("welcome_back", {"welcome_back_after_min": 0}),
    ],
    ids=[
        "all-off-pat",
        "all-off-poke",
        "all-off-double-click",
        "all-off-welcome",
        "pat-off",
        "poke-off",
        "double-click-off",
        "welcome-never",
    ],
)
def test_an_interaction_that_is_switched_off_does_nothing(kind, config):
    handler, assistant, _ = make_handler(**config)

    react(handler, kind)

    assert assistant.events == []


def test_switching_one_reaction_off_leaves_the_others_on():
    handler, assistant, clock = make_handler(head_pat=False)

    react(handler, "head_pat")
    clock.now += 10
    react(handler, "body_touch")

    assert assistant.events == ["speak surprised"]


def test_an_unknown_interaction_is_logged_and_ignored(caplog):
    handler, assistant, _ = make_handler()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        react(handler, "wave")

    assert assistant.events == []
    assert "wave" in caplog.text


# Busy, cooldown, and the welcome-back limit


@pytest.mark.parametrize("kind", ["head_pat", "body_touch", "double_click", "welcome_back"])
def test_nothing_happens_while_she_is_busy(kind):
    handler, assistant, _ = make_handler()
    assistant.busy = True

    react(handler, kind)

    assert assistant.events == []


def test_busy_is_checked_when_the_interaction_is_handled():
    handler, assistant, _ = make_handler()
    assistant.busy = True
    react(handler, "head_pat")

    assistant.busy = False
    react(handler, "head_pat")

    assert assistant.events == ["speak happy"]


def test_a_reaction_within_the_cooldown_of_the_previous_one_is_skipped():
    handler, assistant, clock = make_handler(reaction_cooldown_s=3)

    react(handler, "head_pat")
    clock.now += 2.9
    react(handler, "body_touch")
    clock.now += 0.1
    react(handler, "body_touch")

    assert assistant.events == ["speak happy", "speak surprised"]


def test_the_cooldown_counts_from_the_end_of_the_previous_line():
    handler, assistant, clock = make_handler(reaction_cooldown_s=3)
    assistant.speak_s = 2.0  # the first line ends at 1002
    spoken_at = speak_times(assistant, clock)

    react(handler, "head_pat")
    clock.now = 1004.9
    react(handler, "head_pat")
    clock.now = 1005.0
    react(handler, "head_pat")

    # Counted from the start (1000), the pat at 1004.9 would be answered instead.
    assert spoken_at == [1000.0, 1005.0]


def test_pats_queued_while_she_answers_one_are_dropped_by_the_cooldown():
    handler, assistant, _ = make_handler()

    react(handler, "head_pat", "head_pat", "body_touch", "head_pat")

    assert assistant.events == ["speak happy"]


def test_without_a_cooldown_every_reaction_is_spoken():
    handler, assistant, _ = make_handler(reaction_cooldown_s=0)

    react(handler, "head_pat", "body_touch", "head_pat")

    assert assistant.events == ["speak happy", "speak surprised", "speak happy"]


def test_a_double_click_within_the_cooldown_still_starts_listening_without_a_line():
    handler, assistant, _ = make_handler()

    react(handler, "head_pat", "double_click")

    assert assistant.events == ["speak happy", "listen"]


def test_a_skipped_reaction_does_not_start_a_cooldown():
    handler, assistant, _ = make_handler()
    assistant.busy = True
    react(handler, "head_pat")

    assistant.busy = False
    react(handler, "body_touch")

    assert assistant.events == ["speak surprised"]


def test_she_welcomes_master_back_at_most_once_per_thirty_minutes():
    # No cooldown: only the thirty-minute limit may skip a welcome here.
    handler, assistant, clock = make_handler(reaction_cooldown_s=0)
    spoken_at = speak_times(assistant, clock)

    react(handler, "welcome_back")
    clock.now += WELCOME_BACK_GAP_S - 1
    react(handler, "welcome_back")
    clock.now += 1
    react(handler, "welcome_back")

    assert WELCOME_BACK_GAP_S == 30 * 60
    assert spoken_at == [1000.0, 2800.0]


def test_the_welcome_back_limit_leaves_other_reactions_alone():
    handler, assistant, clock = make_handler()

    react(handler, "welcome_back")
    clock.now += 60
    react(handler, "body_touch")

    assert assistant.events == ["speak happy", "speak surprised"]


def test_a_welcome_back_skipped_while_busy_does_not_count_against_the_limit():
    handler, assistant, clock = make_handler(reaction_cooldown_s=0)
    spoken_at = speak_times(assistant, clock)
    assistant.busy = True
    react(handler, "welcome_back")

    assistant.busy = False
    clock.now += 60
    react(handler, "welcome_back")
    clock.now += 5 * 60
    react(handler, "welcome_back")  # the spoken welcome does count

    assert spoken_at == [1060.0]


# Which line she says


class LastChoice(random.Random):
    """Always picks the last candidate, so a test knows which line comes next."""

    def choice(self, seq):
        return seq[-1]


def test_the_line_is_picked_by_the_injected_rng_without_repeating_the_previous_one():
    handler, assistant, clock = make_handler(
        rng=LastChoice(), lines={"head_pat": ["Satu", "Dua", "Tiga"]}
    )

    for _ in range(3):
        react(handler, "head_pat")
        clock.now += 10

    assert [line for line, _ in assistant.spoken] == ["Tiga", "Dua", "Tiga"]


def test_a_line_never_follows_itself_and_every_line_gets_its_turn():
    handler, assistant, clock = make_handler()

    for _ in range(60):
        react(handler, "head_pat")
        clock.now += 10

    said = [line for line, _ in assistant.spoken]
    assert all(previous != line for previous, line in zip(said, said[1:], strict=False))
    assert set(said) == set(LINES["head_pat"])


def test_repeats_are_avoided_per_kind():
    handler, assistant, clock = make_handler(
        reaction_cooldown_s=0, lines={"head_pat": ["A", "B"], "body_touch": ["A", "B"]}
    )

    for _ in range(4):
        react(handler, "head_pat", "body_touch")
        clock.now += 1

    pats = [line for line, emotion in assistant.spoken if emotion == "happy"]
    pokes = [line for line, emotion in assistant.spoken if emotion == "surprised"]
    assert pats in (["A", "B", "A", "B"], ["B", "A", "B", "A"])
    assert pokes in (["A", "B", "A", "B"], ["B", "A", "B", "A"])


def test_a_kind_with_one_line_says_it_every_time():
    handler, assistant, clock = make_handler(lines={"listen": ["Ya, Master?"]})

    react(handler, "double_click")
    clock.now += 10
    react(handler, "double_click")

    assert assistant.spoken == [("Ya, Master?", "happy"), ("Ya, Master?", "happy")]


def test_the_same_seed_picks_the_same_lines():
    said = []
    for _ in range(2):
        handler, assistant, clock = make_handler(rng=random.Random(42))
        for _ in range(5):
            react(handler, "body_touch")
            clock.now += 10
        said.append(assistant.spoken)

    assert said[0] == said[1]


# The queue and failures


def test_a_full_queue_drops_further_interactions_and_logs_each_drop(caplog):
    handler, assistant, _ = make_handler()

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        for _ in range(QUEUE_SIZE + 2):
            handler.handle("double_click")
    handler.drain()

    assert QUEUE_SIZE == 32
    assert assistant.events.count("listen") == QUEUE_SIZE
    assert sum("full" in record.getMessage() for record in caplog.records) == 2


def test_a_failing_speak_is_logged_and_the_next_reaction_still_works(caplog):
    handler, assistant, clock = make_handler()
    assistant.speak_error = RuntimeError("TTS broke")

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        react(handler, "head_pat")
    assistant.speak_error = None
    clock.now += 10
    react(handler, "body_touch")

    assert "TTS broke" in caplog.text
    assert assistant.events == ["speak happy", "speak surprised"]


def test_a_failing_listen_is_logged_and_nothing_is_said(caplog):
    handler, assistant, clock = make_handler()
    assistant.listen_error = RuntimeError("gate broke")

    with caplog.at_level(logging.ERROR, logger=LOGGER):
        react(handler, "double_click")
    clock.now += 10
    react(handler, "head_pat")

    assert "gate broke" in caplog.text
    assert assistant.events == ["listen", "speak happy"]


# The worker thread


def test_the_worker_reacts_in_its_own_thread():
    handler, assistant, _ = make_handler()
    spoke = threading.Event()
    assistant.on_speak = spoke.set

    with handler:
        handler.handle("head_pat")
        assert spoke.wait(WAIT_S)

    assert assistant.events == ["speak happy"]
    assert assistant.threads[0] is not threading.current_thread()
    assert assistant.threads[0].name == WORKER_NAME


def test_the_worker_survives_a_failing_speak(caplog):
    # No cooldown: the second line must not depend on when the worker finished the first.
    handler, assistant, _ = make_handler(reaction_cooldown_s=0)
    assistant.speak_error = RuntimeError("TTS broke")
    spoke = threading.Semaphore(0)
    assistant.on_speak = spoke.release

    with caplog.at_level(logging.ERROR, logger=LOGGER), handler:
        handler.handle("head_pat")
        assert spoke.acquire(timeout=WAIT_S)
        handler.handle("body_touch")
        assert spoke.acquire(timeout=WAIT_S)

    assert assistant.events == ["speak happy", "speak surprised"]
    assert "TTS broke" in caplog.text


def test_starting_twice_runs_one_worker_and_stop_ends_it():
    handler, _, _ = make_handler()
    before = workers()

    handler.start()
    handler.start()
    running = workers()
    handler.stop()

    assert running == before + 1
    assert workers() == before


def test_handle_after_stop_does_nothing():
    handler, assistant, _ = make_handler()
    handler.start()
    handler.stop()

    handler.handle("head_pat")
    handler.drain()

    assert assistant.events == []


def test_stop_without_start_is_harmless_and_turns_handle_off():
    handler, assistant, _ = make_handler()

    handler.stop()
    react(handler, "head_pat")

    assert assistant.events == []


def test_a_stopped_handler_can_be_started_again():
    handler, assistant, _ = make_handler()
    spoke = threading.Event()
    assistant.on_speak = spoke.set
    handler.start()
    handler.stop()

    with handler:
        handler.handle("body_touch")
        assert spoke.wait(WAIT_S)

    assert assistant.events == ["speak surprised"]


def test_stop_gives_up_waiting_for_a_long_line_and_drops_what_is_queued(monkeypatch, caplog):
    monkeypatch.setattr(interactions, "STOP_TIMEOUT_S", 0.05)
    handler, assistant, clock = make_handler(reaction_cooldown_s=0)
    speaking, release = threading.Event(), threading.Event()

    def hold() -> None:
        speaking.set()
        release.wait(WAIT_S)

    assistant.on_speak = hold
    handler.start()
    handler.handle("head_pat")
    assert speaking.wait(WAIT_S)
    handler.handle("body_touch")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        handler.stop()
    worker = assistant.threads[0]
    still_speaking = worker.is_alive()
    release.set()
    worker.join(WAIT_S)

    assert still_speaking
    assert not worker.is_alive()
    assert assistant.events == ["speak happy"]
    assert caplog.records
