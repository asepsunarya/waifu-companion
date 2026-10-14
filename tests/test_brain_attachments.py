import threading

from waifu.brain.attachments import CameraTurn, TurnImages
from waifu.brain.base import ToolImage


def photo(label: str) -> ToolImage:
    """A tiny stand-in for a camera photo: JPEG start and end markers, never a real image."""
    return ToolImage(
        "image/jpeg", bytes.fromhex("ffd8ffe0") + label.encode() + bytes.fromhex("ffd9")
    )


PHOTO = photo("photo-1")
OTHER_PHOTO = photo("photo-2")


def test_nothing_waits_at_first():
    assert TurnImages().take() == []


def test_a_photo_is_handed_over_once_and_then_forgotten():
    images = TurnImages()
    images.put(PHOTO)

    assert images.take() == [PHOTO]
    assert images.take() == []


def test_a_second_photo_replaces_the_one_waiting():
    images = TurnImages()
    images.put(PHOTO)
    images.put(OTHER_PHOTO)

    assert images.take() == [OTHER_PHOTO]


def test_clear_forgets_the_waiting_photo():
    images = TurnImages()
    images.put(PHOTO)

    images.clear()

    assert images.take() == []


def test_clear_with_nothing_waiting_is_harmless():
    images = TurnImages()

    images.clear()

    assert images.take() == []


def test_threads_putting_and_taking_at_once_never_get_the_same_photo_twice():
    images = TurnImages()
    put = [photo(f"photo-{index}") for index in range(4000)]
    taken: list[ToolImage] = []
    taken_lock = threading.Lock()
    start = threading.Barrier(8)

    def putter(batch: list[ToolImage]) -> None:
        start.wait()
        for image in batch:
            images.put(image)

    def taker() -> None:
        start.wait()
        mine = [image for _ in range(2000) for image in images.take()]
        with taken_lock:
            taken.extend(mine)

    threads = [threading.Thread(target=putter, args=(put[index::4],)) for index in range(4)]
    threads += [threading.Thread(target=taker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    taken += images.take()

    # Every photo is distinct, so a photo handed over twice would shrink the set.
    assert taken
    assert len(set(taken)) == len(taken)
    assert set(taken) <= set(put)


def test_no_turn_is_sent_through_the_phone_camera_at_first():
    assert CameraTurn().active is False


def test_a_camera_turn_lasts_from_its_beginning_to_its_end():
    turn = CameraTurn()

    turn.begin()
    during = turn.active
    turn.end()

    assert during is True
    assert turn.active is False


def test_ending_a_camera_turn_that_never_began_is_harmless():
    turn = CameraTurn()

    turn.end()

    assert turn.active is False
