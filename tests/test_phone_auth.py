import base64
import contextlib
import dataclasses
import hashlib
import hmac
import json
import logging
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta

import pytest

from waifu.phone import auth as phone_auth
from waifu.phone.auth import (
    CODE_LIFETIME,
    LAST_USED_STEP,
    MAX_TRIES,
    TOKEN_BYTES,
    Device,
    PhoneAuth,
    Refusal,
)

START = datetime(2026, 10, 14, 9, 30, 15, 250000)
STAMP = START.replace(microsecond=0)  # times are kept to the second
CORRUPT_NAME = "phone.json.corrupt-20261014-093015"
LOCK_NAME = "phone.json.lock"  # held by every change, so other processes wait for it
FIXED_CODE = "123456"


class Clock:
    def __init__(self, start: datetime = START) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


class Tokens:
    """A fake `secrets.token_bytes`: call n gives n repeated, and the sizes are recorded."""

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def __call__(self, nbytes: int) -> bytes:
        self.sizes.append(nbytes)
        return bytes([len(self.sizes)]) * nbytes


def token_for(call: int) -> str:
    """The token the n-th pairing gets from `Tokens`, url-safe like secrets.token_urlsafe."""
    return base64.urlsafe_b64encode(bytes([call]) * TOKEN_BYTES).rstrip(b"=").decode("ascii")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_auth(tmp_path, clock=None, tokens=None):
    return PhoneAuth(tmp_path / "phone.json", now=clock or Clock(), token_bytes=tokens or Tokens())


def pair(auth, name="Android"):
    return auth.redeem(auth.new_code(), name)


def wrong(code):
    """Another six-digit code."""
    return f"{(int(code) + 1) % 1_000_000:06d}"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def fixed_code(monkeypatch):
    """Every new code is FIXED_CODE; the bounds asked of secrets.randbelow are recorded."""
    bounds = []

    def randbelow(bound):
        bounds.append(bound)
        return int(FIXED_CODE)

    monkeypatch.setattr(phone_auth.secrets, "randbelow", randbelow)
    return bounds


# --- pairing codes -----------------------------------------------------------------------------


def test_the_spec_limits():
    limits = (CODE_LIFETIME, MAX_TRIES, TOKEN_BYTES, LAST_USED_STEP)

    assert limits == (timedelta(minutes=10), 5, 32, timedelta(hours=1))


def test_a_new_code_is_six_random_digits(tmp_path):
    code = make_auth(tmp_path).new_code()

    assert re.fullmatch(r"[0-9]{6}", code)


def test_a_new_code_comes_from_secrets_and_keeps_its_leading_zeros(tmp_path, monkeypatch):
    bounds = []

    def randbelow(bound):
        bounds.append(bound)
        return 42

    monkeypatch.setattr(phone_auth.secrets, "randbelow", randbelow)

    assert make_auth(tmp_path).new_code() == "000042"
    assert bounds == [1_000_000]


def test_a_new_code_is_stored_as_its_sha256_with_its_expiry_and_no_tries(tmp_path, fixed_code):
    auth = make_auth(tmp_path)

    auth.new_code()

    assert read_json(auth.path) == {
        "version": 1,
        "next_id": 1,
        "code": {
            "hash": sha256(FIXED_CODE),
            "expires": (STAMP + CODE_LIFETIME).isoformat(),
            "tries": 0,
        },
        "devices": [],
    }


def test_the_right_code_pairs_a_device_with_a_url_safe_token_of_32_random_bytes(tmp_path):
    tokens = Tokens()
    auth = make_auth(tmp_path, tokens=tokens)

    token = pair(auth)

    assert token == token_for(1)
    assert tokens.sizes == [TOKEN_BYTES]
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)


def test_a_paired_device_is_stored_with_an_id_name_times_and_only_its_token_hash(tmp_path):
    auth = make_auth(tmp_path)

    token = pair(auth, "HP Master")

    assert read_json(auth.path) == {
        "version": 1,
        "next_id": 2,
        "code": None,
        "devices": [
            {
                "id": "d1",
                "name": "HP Master",
                "token_hash": sha256(token),
                "created": STAMP.isoformat(),
                "last_used": STAMP.isoformat(),
            }
        ],
    }


def test_a_code_pairs_only_one_device(tmp_path):
    auth = make_auth(tmp_path)
    code = auth.new_code()
    auth.redeem(code, "Android")

    assert auth.redeem(code, "Android") is Refusal.NO_CODE
    assert [device.id for device in auth.devices()] == ["d1"]


def test_a_code_is_valid_for_ten_minutes(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    code = auth.new_code()

    clock.advance(minutes=9, seconds=59)

    assert auth.redeem(code, "Android") == token_for(1)


def test_an_expired_code_is_refused(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    code = auth.new_code()

    clock.advance(minutes=10)

    assert auth.redeem(code, "Android") is Refusal.EXPIRED
    assert auth.devices() == []


def test_a_code_is_refused_when_the_clock_went_back_since_it_was_made(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    code = auth.new_code()

    clock.advance(minutes=-1)

    assert auth.redeem(code, "Android") is Refusal.EXPIRED


def test_redeem_says_why_it_refused_a_code(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    assert auth.redeem(FIXED_CODE, "Android") is Refusal.NO_CODE
    code = auth.new_code()

    tries = [auth.redeem(wrong(code), "Android") for _ in range(MAX_TRIES)]
    after_burning = auth.redeem(code, "Android")
    code = auth.new_code()
    clock.advance(minutes=10)

    assert tries == [Refusal.WRONG] * (MAX_TRIES - 1) + [Refusal.BURNED]
    assert after_burning is Refusal.NO_CODE
    assert auth.redeem(code, "Android") is Refusal.EXPIRED


def test_redeeming_without_a_code_is_refused_and_creates_no_file(tmp_path):
    auth = make_auth(tmp_path)

    assert auth.redeem(FIXED_CODE, "Android") is Refusal.NO_CODE
    assert not auth.path.exists()


def test_a_wrong_code_counts_a_try_in_the_file(tmp_path):
    auth = make_auth(tmp_path)
    code = auth.new_code()

    assert auth.redeem(wrong(code), "Android") is Refusal.WRONG

    assert read_json(auth.path)["code"]["tries"] == 1
    assert auth.devices() == []


def test_the_fifth_try_may_still_be_the_right_code(tmp_path):
    auth = make_auth(tmp_path)
    code = auth.new_code()
    for _ in range(MAX_TRIES - 1):
        auth.redeem(wrong(code), "Android")

    assert auth.redeem(code, "Android") == token_for(1)


def test_five_wrong_tries_burn_the_code(tmp_path):
    auth = make_auth(tmp_path)
    code = auth.new_code()

    tries = [auth.redeem(wrong(code), "Android") for _ in range(MAX_TRIES)]

    assert tries == [Refusal.WRONG] * (MAX_TRIES - 1) + [Refusal.BURNED]
    assert read_json(auth.path)["code"] is None
    assert auth.redeem(code, "Android") is Refusal.NO_CODE
    assert auth.devices() == []


@pytest.mark.parametrize("attempt", ["", "12345", "1234567", "abcdef", " 123456", "\ud800"])
def test_a_malformed_code_is_a_wrong_try_and_never_raises(tmp_path, fixed_code, attempt):
    auth = make_auth(tmp_path)
    auth.new_code()

    assert auth.redeem(attempt, "Android") is Refusal.WRONG

    assert read_json(auth.path)["code"]["tries"] == 1


def test_tries_are_shared_through_the_file_by_every_store(tmp_path):
    first, second = make_auth(tmp_path), make_auth(tmp_path)
    code = first.new_code()
    for _ in range(3):
        first.redeem(wrong(code), "Android")
    for _ in range(2):
        second.redeem(wrong(code), "Android")

    assert first.redeem(code, "Android") is Refusal.NO_CODE


def test_a_new_code_replaces_the_old_one_and_its_tries(tmp_path):
    auth = make_auth(tmp_path)
    old = auth.new_code()
    for _ in range(MAX_TRIES - 1):
        auth.redeem(wrong(old), "Android")
    new = auth.new_code()
    while new == old:  # one chance in a million
        new = auth.new_code()

    assert read_json(auth.path)["code"]["tries"] == 0
    assert auth.redeem(old, "Android") is Refusal.WRONG
    assert auth.redeem(new, "Android") == token_for(1)


def test_a_new_code_restarts_the_ten_minutes(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    auth.new_code()
    clock.advance(minutes=8)
    code = auth.new_code()

    clock.advance(minutes=8)

    assert auth.redeem(code, "Android") == token_for(1)


def test_a_new_code_leaves_the_paired_devices_alone(tmp_path):
    auth = make_auth(tmp_path)
    token = pair(auth)

    auth.new_code()

    assert auth.check(token).id == "d1"


def test_a_code_is_compared_by_its_hash_in_constant_time(tmp_path, monkeypatch, fixed_code):
    auth = make_auth(tmp_path)
    auth.new_code()
    calls = []
    real = hmac.compare_digest

    def spy(left, right):
        calls.append({left, right})
        return real(left, right)

    monkeypatch.setattr(phone_auth.hmac, "compare_digest", spy)

    auth.redeem(wrong(FIXED_CODE), "Android")

    assert calls == [{sha256(wrong(FIXED_CODE)), sha256(FIXED_CODE)}]


# --- devices -----------------------------------------------------------------------------------


def test_check_knows_a_paired_device_by_its_token(tmp_path):
    auth = make_auth(tmp_path)
    token = pair(auth, "HP Master")

    assert auth.check(token) == Device(id="d1", name="HP Master", created=STAMP, last_used=STAMP)


@pytest.mark.parametrize("token", ["", "x" * 43, token_for(2), "\ud800"])
def test_check_refuses_a_token_it_does_not_know(tmp_path, token):
    auth = make_auth(tmp_path)
    pair(auth)

    assert auth.check(token) is None


def test_check_without_a_file_refuses_and_creates_nothing(tmp_path):
    auth = make_auth(tmp_path)

    assert auth.check(token_for(1)) is None
    assert not auth.path.exists()


def test_each_device_has_its_own_token(tmp_path):
    auth = make_auth(tmp_path)
    first, second = pair(auth, "HP"), pair(auth, "Tablet")

    assert auth.check(first).name == "HP"
    assert auth.check(second).name == "Tablet"


def test_check_compares_every_device_hash_in_constant_time(tmp_path, monkeypatch):
    auth = make_auth(tmp_path)
    token = pair(auth)
    pair(auth)
    calls = []
    real = hmac.compare_digest

    def spy(left, right):
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr(phone_auth.hmac, "compare_digest", spy)

    assert auth.check(token).id == "d1"
    assert len(calls) == 2
    assert (sha256(token), sha256(token)) in calls


def test_check_updates_last_used_at_most_once_an_hour(tmp_path, monkeypatch):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    token = pair(auth)
    saved = auth.path.read_bytes()

    def refused_replace(src, dst):
        raise AssertionError("last_used is still fresh")

    clock.advance(minutes=59)
    with monkeypatch.context() as patch:
        patch.setattr(phone_auth.os, "replace", refused_replace)
        assert auth.check(token).last_used == STAMP
    assert auth.path.read_bytes() == saved

    clock.advance(minutes=1)
    later = STAMP + LAST_USED_STEP

    assert auth.check(token).last_used == later
    assert read_json(auth.path)["devices"][0]["last_used"] == later.isoformat()
    assert auth.devices()[0].created == STAMP


def test_check_corrects_a_last_used_an_hour_or_more_ahead_of_the_clock(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    token = pair(auth)

    clock.advance(hours=-1)

    assert auth.check(token).last_used == STAMP - LAST_USED_STEP


def test_devices_lists_every_paired_device_oldest_first(tmp_path):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    pair(auth, "HP")
    clock.advance(days=1)
    pair(auth, "Tablet")

    assert auth.devices() == [
        Device(id="d1", name="HP", created=STAMP, last_used=STAMP),
        Device(
            id="d2",
            name="Tablet",
            created=STAMP + timedelta(days=1),
            last_used=STAMP + timedelta(days=1),
        ),
    ]


def test_a_device_value_holds_no_token_or_hash():
    assert [field.name for field in dataclasses.fields(Device)] == [
        "id",
        "name",
        "created",
        "last_used",
    ]


def test_a_device_is_frozen():
    device = Device(id="d1", name="HP", created=STAMP, last_used=STAMP)

    with pytest.raises(dataclasses.FrozenInstanceError):
        device.name = "Tablet"  # type: ignore[misc]


def test_forget_revokes_a_device_and_its_token(tmp_path):
    auth = make_auth(tmp_path)
    first, second = pair(auth, "HP"), pair(auth, "Tablet")

    assert auth.forget("d1") is True

    assert auth.check(first) is None
    assert auth.check(second).id == "d2"
    assert [device.id for device in auth.devices()] == ["d2"]


def test_forgetting_an_unknown_device_returns_false_and_writes_nothing(tmp_path, monkeypatch):
    auth = make_auth(tmp_path)
    pair(auth)

    def refused_replace(src, dst):
        raise AssertionError("nothing to forget")

    monkeypatch.setattr(phone_auth.os, "replace", refused_replace)

    assert auth.forget("d2") is False
    assert auth.forget("m1") is False


def test_device_ids_count_up_and_are_never_reused(tmp_path):
    auth = make_auth(tmp_path)
    pair(auth)
    pair(auth)
    auth.forget("d2")

    pair(auth)

    assert [device.id for device in auth.devices()] == ["d1", "d3"]


# --- the store file ----------------------------------------------------------------------------


def test_reading_never_creates_anything(tmp_path):
    auth = make_auth(tmp_path)

    assert auth.devices() == []
    assert auth.check(token_for(1)) is None

    assert list(tmp_path.iterdir()) == []


def test_a_refused_redeem_or_forget_never_creates_the_file(tmp_path):
    auth = make_auth(tmp_path)

    assert auth.redeem(FIXED_CODE, "Android") is Refusal.NO_CODE
    assert auth.forget("d1") is False

    assert {path.name for path in tmp_path.iterdir()} <= {LOCK_NAME}


def test_every_call_rereads_the_file_so_another_process_sees_the_changes(tmp_path):
    tokens = Tokens()
    server, terminal = make_auth(tmp_path, tokens=tokens), make_auth(tmp_path, tokens=tokens)

    code = terminal.new_code()
    token = server.redeem(code, "Android")
    assert [device.id for device in terminal.devices()] == ["d1"]

    terminal.forget("d1")
    assert server.check(token) is None


def test_the_file_never_holds_a_plain_code_or_token(tmp_path, fixed_code):
    clock = Clock()
    auth = make_auth(tmp_path, clock)
    secrets_seen = [FIXED_CODE, wrong(FIXED_CODE)]

    def assert_no_secret_in_file():
        text = auth.path.read_text(encoding="utf-8")
        for secret in secrets_seen:
            assert secret not in text

    auth.new_code()
    assert_no_secret_in_file()
    auth.redeem(wrong(FIXED_CODE), "Android")
    assert_no_secret_in_file()
    secrets_seen.append(auth.redeem(FIXED_CODE, "Android"))
    assert_no_secret_in_file()
    clock.advance(hours=1)
    auth.check(secrets_seen[-1])
    auth.new_code()
    assert_no_secret_in_file()


def test_no_log_line_holds_a_code_a_token_or_their_hashes(tmp_path, caplog, fixed_code):
    clock = Clock()
    auth = make_auth(tmp_path, clock)

    with caplog.at_level(logging.DEBUG, logger="waifu.phone.auth"):
        auth.new_code()
        for _ in range(MAX_TRIES):
            auth.redeem(wrong(FIXED_CODE), "Android")
        auth.new_code()
        token = auth.redeem(FIXED_CODE, "Android")
        clock.advance(hours=1)
        auth.check(token)
        auth.check(token_for(9))
        auth.forget("d1")

    assert caplog.records
    for secret in (FIXED_CODE, wrong(FIXED_CODE), token, sha256(FIXED_CODE), sha256(token)):
        assert secret not in caplog.text


def test_pairing_events_are_logged_for_the_owner(tmp_path, caplog):
    clock = Clock()
    auth = make_auth(tmp_path, clock)

    with caplog.at_level(logging.INFO, logger="waifu.phone.auth"):
        code = auth.new_code()
        for _ in range(MAX_TRIES):
            auth.redeem(wrong(code), "Android")
        code = auth.new_code()
        auth.redeem(code, "HP Master")
        auth.forget("d1")

    assert "burned" in caplog.text
    assert "d1 (HP Master)" in caplog.text
    assert "Forgot phone device d1" in caplog.text


def test_a_file_saved_with_a_byte_order_mark_is_read(tmp_path):
    auth = make_auth(tmp_path)
    token = pair(auth)
    auth.path.write_bytes(b"\xef\xbb\xbf" + auth.path.read_bytes())

    assert auth.check(token).id == "d1"


def test_a_hand_written_id_at_or_beyond_next_id_is_never_handed_out_again(tmp_path):
    auth = make_auth(tmp_path)
    auth.path.write_text(json.dumps(file_content(next_id=1)), encoding="utf-8")

    pair(auth)

    assert [device.id for device in auth.devices()] == ["d1", "d2"]


def test_the_folder_is_created_on_the_first_write_and_a_path_with_spaces_works(tmp_path):
    path = tmp_path / "Data Cyrene" / "HP Master" / "pasangan HP.json"
    auth = PhoneAuth(path, now=Clock(), token_bytes=Tokens())

    token = pair(auth)

    assert PhoneAuth(path).check(token).id == "d1"


def test_the_store_accepts_a_path_given_as_text(tmp_path):
    auth = PhoneAuth(str(tmp_path / "phone.json"), now=Clock(), token_bytes=Tokens())

    pair(auth)

    assert auth.path == tmp_path / "phone.json"
    assert auth.path.exists()


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    auth = make_auth(tmp_path)

    pair(auth)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["phone.json", LOCK_NAME]


def test_a_failed_replace_keeps_the_previous_file_intact(tmp_path, monkeypatch):
    auth = make_auth(tmp_path)
    token = pair(auth)
    saved = auth.path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(phone_auth.os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        auth.new_code()
    monkeypatch.undo()

    assert auth.path.read_bytes() == saved
    assert sorted(path.name for path in tmp_path.iterdir()) == ["phone.json", LOCK_NAME]
    assert auth.check(token).id == "d1"


# --- concurrency -------------------------------------------------------------------------------


def run_in_threads(target, count=8):
    errors = []

    def guarded(worker):
        try:
            target(worker)
        except Exception as exc:  # surfaced by the caller's assertion
            errors.append(exc)

    threads = [threading.Thread(target=guarded, args=(worker,)) for worker in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_a_code_redeemed_by_many_threads_at_once_pairs_exactly_one_device(tmp_path):
    tokens = Tokens()
    stores = [make_auth(tmp_path, tokens=tokens), make_auth(tmp_path, tokens=tokens)]
    code = stores[0].new_code()
    results = []

    def redeem(worker):
        results.append(stores[worker % 2].redeem(code, f"HP {worker}"))

    assert run_in_threads(redeem) == []
    assert [token for token in results if isinstance(token, str)] == [token_for(1)]
    assert len(stores[1].devices()) == 1


def test_wrong_tries_from_many_threads_are_all_counted(tmp_path):
    stores = [make_auth(tmp_path), make_auth(tmp_path)]
    code = stores[0].new_code()

    def try_wrong(worker):
        stores[worker % 2].redeem(wrong(code), "Android")

    assert run_in_threads(try_wrong, count=MAX_TRIES - 1) == []
    assert read_json(stores[0].path)["code"]["tries"] == MAX_TRIES - 1

    assert stores[1].redeem(wrong(code), "Android") is Refusal.BURNED
    assert stores[0].redeem(code, "Android") is Refusal.NO_CODE


# Run by another Python process: `waifu phone forget d1` on the pairing file at argv[1]. It says
# when it is about to forget, then whether it did.
FORGET_IN_ANOTHER_PROCESS = """
import sys
from waifu.phone.auth import PhoneAuth
print("forgetting", flush=True)
print(PhoneAuth(sys.argv[1]).forget("d1"), flush=True)
"""
# How long a write waits for that process to forget: plenty unless the file lock holds it back.
OTHER_PROCESS_WAIT_S = 1.5


def test_a_forget_from_another_process_waits_for_a_write_in_flight_and_stays(tmp_path, monkeypatch):
    # `waifu phone forget` runs in its own process while `waifu run` answers the phone: a write
    # of the server, from what it read before the forget, must never bring the phone back.
    clock = Clock()
    server = make_auth(tmp_path, clock)
    token = pair(server)
    clock.advance(hours=1)  # the phone's next request saves its last use
    real_write = phone_auth.write_json_atomic
    forgetting = []

    def write_while_another_process_forgets(path, data):
        other = subprocess.Popen(
            [sys.executable, "-c", FORGET_IN_ANOTHER_PROCESS, str(path)],
            stdout=subprocess.PIPE,
            text=True,
        )
        forgetting.append(other)
        assert other.stdout.readline().strip() == "forgetting"
        with contextlib.suppress(subprocess.TimeoutExpired):
            other.wait(timeout=OTHER_PROCESS_WAIT_S)
        real_write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(phone_auth, "write_json_atomic", write_while_another_process_forgets)
        assert server.check(token).id == "d1"  # the request in flight is still answered

    [other] = forgetting
    said, _ = other.communicate(timeout=30)
    assert said.split() == ["True"]
    assert server.devices() == []
    assert server.check(token) is None


def test_last_used_updates_from_many_threads_lose_no_device(tmp_path):
    clock, tokens = Clock(), Tokens()
    stores = [make_auth(tmp_path, clock, tokens), make_auth(tmp_path, clock, tokens)]
    paired = [pair(stores[0], f"HP {number}") for number in range(8)]
    clock.advance(hours=1)

    def check(worker):
        assert stores[worker % 2].check(paired[worker]) is not None

    assert run_in_threads(check) == []
    devices = stores[0].devices()
    assert [device.id for device in devices] == [f"d{number}" for number in range(1, 9)]
    assert {device.last_used for device in devices} == {STAMP + LAST_USED_STEP}


# --- corrupt files -----------------------------------------------------------------------------

VALID_CODE = {"hash": "b" * 64, "expires": "2026-10-14T09:40:15", "tries": 0}
VALID_DEVICE = {
    "id": "d1",
    "name": "Android",
    "token_hash": "a" * 64,
    "created": "2026-10-14T09:30:15",
    "last_used": "2026-10-14T09:30:15",
}


def file_content(**changes):
    return {"version": 1, "next_id": 2, "code": VALID_CODE, "devices": [VALID_DEVICE]} | changes


def file_with(**changes):
    return json.dumps(file_content(**changes)).encode()


def code_with(**changes):
    return VALID_CODE | changes


def device_with(**changes):
    return [VALID_DEVICE | changes]


def test_a_hand_written_valid_file_is_read(tmp_path):
    auth = make_auth(tmp_path)
    auth.path.write_bytes(file_with(code=None))

    assert auth.devices() == [Device(id="d1", name="Android", created=STAMP, last_used=STAMP)]


CORRUPT_CONTENTS = {
    "empty file": b"",
    "not json": b"{not json",
    "not utf-8": b'{"version": 1, "next_id": 1, "code": null, "devices": [], "\xff": 1}',
    "a list": b"[]",
    "missing devices": json.dumps({"version": 1, "next_id": 1, "code": None}).encode(),
    "an extra key": file_with(owner="Master"),
    "another version": file_with(version=2),
    "version true": file_with(version=True),
    "next_id zero": file_with(next_id=0),
    "next_id text": file_with(next_id="2"),
    "code a list": file_with(code=[]),
    "code with an extra key": file_with(code=code_with(code=FIXED_CODE)),
    "code without tries": file_with(code={"hash": "b" * 64, "expires": "2026-10-14T09:40:15"}),
    "code hash uppercase": file_with(code=code_with(hash="B" * 64)),
    "code hash short": file_with(code=code_with(hash="b" * 63)),
    "code hash a number": file_with(code=code_with(hash=123456)),
    "code expiry not a time": file_with(code=code_with(expires="soon")),
    "code expiry with a time zone": file_with(code=code_with(expires="2026-10-14T09:40:15+07:00")),
    "tries negative": file_with(code=code_with(tries=-1)),
    "tries used up": file_with(code=code_with(tries=MAX_TRIES)),
    "tries true": file_with(code=code_with(tries=True)),
    "tries a float": file_with(code=code_with(tries=1.0)),
    "devices not a list": file_with(devices={"d1": VALID_DEVICE}),
    "device not an object": file_with(devices=["d1"]),
    "device with an extra key": file_with(devices=device_with(token="plain")),
    "device without last_used": file_with(
        devices=[{key: value for key, value in VALID_DEVICE.items() if key != "last_used"}]
    ),
    "device id of a memory note": file_with(devices=device_with(id="m1")),
    "device id zero": file_with(devices=device_with(id="d0")),
    "device id a number": file_with(devices=device_with(id=1)),
    "device name a number": file_with(devices=device_with(name=7)),
    "token hash not hex": file_with(devices=device_with(token_hash="z" * 64)),
    "token hash null": file_with(devices=device_with(token_hash=None)),
    "created not a time": file_with(devices=device_with(created="kemarin")),
    "last_used with a time zone": file_with(
        devices=device_with(last_used="2026-10-14T09:30:15+00:00")
    ),
    "two devices share an id": file_with(devices=[VALID_DEVICE, VALID_DEVICE]),
}


@pytest.mark.parametrize("content", CORRUPT_CONTENTS.values(), ids=CORRUPT_CONTENTS.keys())
def test_a_corrupt_file_is_moved_aside_logged_and_every_phone_must_pair_again(
    tmp_path, caplog, content
):
    auth = make_auth(tmp_path)
    auth.path.write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="waifu.phone.auth"):
        assert auth.devices() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == content
    assert not auth.path.exists()
    assert CORRUPT_NAME in caplog.text


def test_the_log_of_a_corrupt_file_never_quotes_it(tmp_path, caplog):
    auth = make_auth(tmp_path)
    auth.path.write_bytes(
        file_with(code=code_with(hash="Y" * 64), devices=device_with(token_hash="Z" * 64))
    )

    with caplog.at_level(logging.DEBUG, logger="waifu.phone.auth"):
        auth.devices()

    assert CORRUPT_NAME in caplog.text
    assert "YYYY" not in caplog.text
    assert "ZZZZ" not in caplog.text


def test_a_corrupt_file_refuses_every_token_it_held(tmp_path):
    auth = make_auth(tmp_path)
    token = pair(auth)
    auth.path.write_bytes(auth.path.read_bytes()[:-5])

    assert auth.check(token) is None


def test_a_corrupt_file_is_never_overwritten_by_a_later_write(tmp_path):
    auth = make_auth(tmp_path)
    auth.path.write_bytes(b"{not json")

    token = pair(auth)

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"{not json"
    assert auth.check(token).id == "d1"


def test_a_corrupt_copy_with_the_same_stamp_gets_a_counter(tmp_path):
    auth = make_auth(tmp_path)
    (tmp_path / CORRUPT_NAME).write_bytes(b"first")
    (tmp_path / f"{CORRUPT_NAME}-2").write_bytes(b"second")
    auth.path.write_bytes(b"third")

    assert auth.devices() == []

    assert (tmp_path / CORRUPT_NAME).read_bytes() == b"first"
    assert (tmp_path / f"{CORRUPT_NAME}-2").read_bytes() == b"second"
    assert (tmp_path / f"{CORRUPT_NAME}-3").read_bytes() == b"third"


def test_nothing_is_written_when_a_corrupt_file_cannot_be_moved(tmp_path, monkeypatch):
    auth = make_auth(tmp_path)
    auth.path.write_bytes(b"{not json")

    def failing_rename(src, dst):
        raise PermissionError("file in use")

    monkeypatch.setattr(phone_auth.os, "rename", failing_rename)
    with pytest.raises(OSError, match="file in use"):
        auth.new_code()

    assert auth.path.read_bytes() == b"{not json"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["phone.json", LOCK_NAME]
