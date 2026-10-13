"""Which phones may use Cyrene: pairing codes and paired devices.

`waifu phone pair` makes a six-digit code (`new_code`) that the owner types into the phone
page within ten minutes; the server hands it to `redeem`, which pairs the phone and returns
its device token once, for the page's cookie. Every later request shows that token to
`check`. `waifu phone devices` lists the devices and `waifu phone forget` revokes one.

Neither a code nor a token is ever written down or logged: the file keeps only their
SHA-256 hashes, and they are compared in constant time. Five wrong tries burn a code, and
pairing one device uses it up. A token is 32 random bytes, so its plain
hash is enough; a code's hash only keeps it from being read off the file, and its ten
minutes and five tries are what protect it.

The file follows the rules of `memory.py`: every call re-reads it, so a running Cyrene
sees the code `waifu phone pair` made in another process. A call that changes the file
holds it from its read until after its write: one lock for every store in this process,
and an OS lock on `<file>.lock` next to it against other processes, so `waifu phone forget`
is never undone by a write `waifu run` based on what it read before. A write goes to a
temp file in the same folder, reaches the disk, and then replaces the file. Reading never
creates it, and a file that is not a pairing state is moved aside before anything is
written: every phone then pairs again, and the file is never overwritten.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import enum
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from waifu.gacha.files import write_json_atomic

logger = logging.getLogger(__name__)

FILE_VERSION = 1
CODE_DIGITS = 6
CODE_LIFETIME = timedelta(minutes=10)
MAX_TRIES = 5  # the wrong tries that burn a code
TOKEN_BYTES = 32
# `check` saves a device's last use only when it is this far from the saved one, so the
# page's many requests do not each rewrite the file.
LAST_USED_STEP = timedelta(hours=1)

_HASH = re.compile(r"[0-9a-f]{64}")  # a SHA-256 hex digest as written
_DEVICE_ID = re.compile(r"d[1-9][0-9]*")
_FILE_KEYS = {"version", "next_id", "code", "devices"}
_CODE_KEYS = {"hash", "expires", "tries"}
_DEVICE_KEYS = {"id", "name", "token_hash", "created", "last_used"}

# One lock for every store: two stores on the same file in this process must not lose
# each other's changes, miss a wrong try, or pair two devices with one code.
_LOCK = threading.Lock()
# O_BINARY keeps Windows from translating anything; it does not exist elsewhere.
_LOCK_FILE_FLAGS = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)

if sys.platform == "win32":
    import msvcrt

    # The lock file is never read or written, so its position stays at byte 0, the byte locked.
    # LK_LOCK tries again once a second, then raises OSError after ten tries.
    def _lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _unlock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class Refusal(enum.Enum):
    """Why `redeem` refused a code."""

    NO_CODE = "no code is waiting"
    EXPIRED = "the code expired, or the clock went back"
    WRONG = "a wrong code; the waiting code takes more tries"
    BURNED = "a wrong code that used up the tries and burned the waiting code"


@dataclass(frozen=True)
class Device:
    id: str  # "d1", "d2", ... never reused
    name: str
    created: datetime  # local time, to the second
    last_used: datetime


@dataclass(frozen=True)
class _Code:
    code_hash: str
    expires: datetime
    tries: int  # wrong tries so far


@dataclass(frozen=True)
class _Paired:
    device: Device
    token_hash: str


@dataclass(frozen=True)
class _State:
    next_id: int
    code: _Code | None
    devices: tuple[_Paired, ...]


class PhoneAuth:
    def __init__(
        self,
        path: Path | str,
        *,
        now: Callable[[], datetime] = datetime.now,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self._token_bytes = token_bytes

    def new_code(self) -> str:
        """A fresh pairing code, valid for CODE_LIFETIME; it replaces any older code."""
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        expires = self._second() + CODE_LIFETIME
        with self._changing() as state:
            self._write(dataclasses.replace(state, code=_Code(_sha256(code), expires, 0)))
        logger.info("Made a phone pairing code, valid until %s", f"{expires:%H:%M:%S}")
        return code

    def redeem(self, code: str, device_name: str) -> str | Refusal:
        """Pair a device with the waiting code and return its token, or why it was refused.

        A wrong code counts a try; MAX_TRIES of them burn the code. An expired code is
        refused, and so is one made later than now (the clock went back).
        """
        attempt = _sha256(code)
        with self._changing() as state:
            now = self._second()
            waiting = state.code
            if waiting is None:
                logger.warning("Refused a phone pairing code: none is waiting")
                return Refusal.NO_CODE
            if not now < waiting.expires <= now + CODE_LIFETIME:
                logger.warning("Refused a phone pairing code: it expired or the clock went back")
                return Refusal.EXPIRED
            if not hmac.compare_digest(attempt, waiting.code_hash):
                return self._count_wrong_try(state, waiting)
            token = _url_safe(self._token_bytes(TOKEN_BYTES))
            device = Device(f"d{state.next_id}", device_name, now, now)
            paired = _Paired(device, _sha256(token))
            self._write(_State(state.next_id + 1, None, (*state.devices, paired)))
        logger.info("Paired phone device %s (%s)", device.id, device.name)
        return token

    def check(self, token: str) -> Device | None:
        """The paired device `token` belongs to, or None.

        Its last use is saved when it is LAST_USED_STEP or more away from the saved one.
        """
        token_hash = _sha256(token)
        with _LOCK:
            found = _paired_with(self._read(), token_hash)
            if found is None or abs(self._second() - found.device.last_used) < LAST_USED_STEP:
                return None if found is None else found.device
        # Saving the last use changes the file: it is read again under the file lock, since
        # another process may have changed it meanwhile (even forgotten this device).
        with self._changing() as state:
            found = _paired_with(state, token_hash)
            if found is None:
                return None
            used = _Paired(
                dataclasses.replace(found.device, last_used=self._second()), found.token_hash
            )
            devices = tuple(used if paired is found else paired for paired in state.devices)
            self._write(dataclasses.replace(state, devices=devices))
            return used.device

    def devices(self) -> list[Device]:
        """Every paired device, oldest first."""
        with _LOCK:
            return [paired.device for paired in self._read().devices]

    def forget(self, device_id: str) -> bool:
        """Revoke device `device_id`; False when no such device is paired."""
        with self._changing() as state:
            kept = tuple(paired for paired in state.devices if paired.device.id != device_id)
            if len(kept) == len(state.devices):
                return False
            self._write(dataclasses.replace(state, devices=kept))
        logger.info("Forgot phone device %s", device_id)
        return True

    def _count_wrong_try(self, state: _State, waiting: _Code) -> Refusal:
        tries = waiting.tries + 1
        if tries < MAX_TRIES:
            self._write(dataclasses.replace(state, code=dataclasses.replace(waiting, tries=tries)))
            logger.warning("Refused a wrong phone pairing code (try %d of %d)", tries, MAX_TRIES)
            return Refusal.WRONG
        self._write(dataclasses.replace(state, code=None))
        logger.warning("Refused a wrong phone pairing code; burned it after %d tries", tries)
        return Refusal.BURNED

    @contextlib.contextmanager
    def _changing(self) -> Iterator[_State]:
        """The state, held against every other store from this read until the block ends.

        In this process `_LOCK` holds it; other processes wait for the OS lock on
        `<file>.lock`, which is made (with its folder) when missing and never deleted:
        deleting it while another process opens it would race.
        """
        with _LOCK:
            lock_path = self.path.with_name(f"{self.path.name}.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, _LOCK_FILE_FLAGS)
            try:
                _lock(fd)
                try:
                    yield self._read()
                finally:
                    _unlock(fd)
            finally:
                os.close(fd)

    def _second(self) -> datetime:
        return self._now().replace(microsecond=0)

    def _read(self) -> _State:
        """The pairing state; a missing file is an empty one and stays missing."""
        try:
            # utf-8-sig: Notepad may save the file with a byte order mark.
            return _parse(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except FileNotFoundError:
            return _State(1, None, ())
        except ValueError as problem:  # also bad JSON and bad UTF-8
            self._set_aside(problem)
            return _State(1, None, ())

    def _set_aside(self, problem: ValueError) -> None:
        """Rename a file that is not a pairing state, so that no write ever replaces it."""
        name = f"{self.path.name}.corrupt-{self._now():%Y%m%d-%H%M%S}"
        target = self.path.with_name(name)
        counter = 2
        while target.exists():
            target = self.path.with_name(f"{name}-{counter}")
            counter += 1
        try:
            os.rename(self.path, target)
        except OSError as exc:
            logger.error(
                "Phone pairing file %s is not a pairing state (%s) and could not be moved "
                "aside: %s",
                self.path,
                problem,
                exc,
            )
            raise
        logger.error(
            "Phone pairing file %s is not a pairing state (%s); moved it to %s, so every "
            "phone must pair again",
            self.path,
            problem,
            target,
        )

    def _write(self, state: _State) -> None:
        write_json_atomic(
            self.path,
            {
                "version": FILE_VERSION,
                "next_id": state.next_id,
                "code": None if state.code is None else _dump_code(state.code),
                "devices": [_dump_device(paired) for paired in state.devices],
            },
        )


def _paired_with(state: _State, token_hash: str) -> _Paired | None:
    """The paired device whose token hash is `token_hash`, or None.

    Every hash is compared, so the time taken does not tell which one matched.
    """
    found = None
    for paired in state.devices:
        if hmac.compare_digest(token_hash, paired.token_hash):
            found = paired
    return found


def _sha256(text: str) -> str:
    # surrogatepass: a lone surrogate from a JSON body is hashed, never an error.
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _url_safe(raw: bytes) -> str:
    """`raw` as unpadded url-safe Base64, like secrets.token_urlsafe (fit for a cookie)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _dump_code(code: _Code) -> dict[str, str | int]:
    return {"hash": code.code_hash, "expires": code.expires.isoformat(), "tries": code.tries}


def _dump_device(paired: _Paired) -> dict[str, str]:
    device = paired.device
    return {
        "id": device.id,
        "name": device.name,
        "token_hash": paired.token_hash,
        "created": device.created.isoformat(),
        "last_used": device.last_used.isoformat(),
    }


def _parse(raw: object) -> _State:
    """The pairing state in `raw`; ValueError says what is wrong, never a value."""
    if not isinstance(raw, dict) or raw.keys() != _FILE_KEYS:
        raise ValueError(f"expected an object with exactly the keys {sorted(_FILE_KEYS)}")
    version, next_id, entries = raw["version"], raw["next_id"], raw["devices"]
    if type(version) is not int or version != FILE_VERSION:
        raise ValueError(f"version must be {FILE_VERSION}")
    if type(next_id) is not int or next_id < 1:
        raise ValueError("next_id must be a positive integer")
    if not isinstance(entries, list):
        raise ValueError("devices must be a list")
    devices = tuple(_parse_device(index, entry) for index, entry in enumerate(entries))
    numbers = [int(paired.device.id[1:]) for paired in devices]
    if len(set(numbers)) != len(numbers):
        raise ValueError("two devices share an id")
    # An id written by hand at or beyond next_id must not be handed out again.
    next_id = max([next_id, *(number + 1 for number in numbers)])
    return _State(next_id, None if raw["code"] is None else _parse_code(raw["code"]), devices)


def _parse_code(raw: object) -> _Code:
    if not isinstance(raw, dict) or raw.keys() != _CODE_KEYS:
        raise ValueError(
            f"code must be null or an object with exactly the keys {sorted(_CODE_KEYS)}"
        )
    tries = raw["tries"]
    if type(tries) is not int or not 0 <= tries < MAX_TRIES:
        raise ValueError(f"code.tries must be an integer from 0 to {MAX_TRIES - 1}")
    return _Code(
        _parse_hash(raw["hash"], "code.hash"), _parse_time(raw["expires"], "code.expires"), tries
    )


def _parse_device(index: int, entry: object) -> _Paired:
    where = f"devices[{index}]"
    if not isinstance(entry, dict) or entry.keys() != _DEVICE_KEYS:
        raise ValueError(f"{where} must be an object with exactly the keys {sorted(_DEVICE_KEYS)}")
    device_id, name = entry["id"], entry["name"]
    if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
        raise ValueError(f"{where}.id must look like d1")
    if not isinstance(name, str):
        raise ValueError(f"{where}.name must be a string")
    device = Device(
        device_id,
        name,
        _parse_time(entry["created"], f"{where}.created"),
        _parse_time(entry["last_used"], f"{where}.last_used"),
    )
    return _Paired(device, _parse_hash(entry["token_hash"], f"{where}.token_hash"))


def _parse_hash(value: object, where: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{where} must be a lowercase SHA-256 hex digest")
    return value


def _parse_time(value: object, where: str) -> datetime:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed
    raise ValueError(f"{where} must be a local ISO 8601 time without a time zone")
