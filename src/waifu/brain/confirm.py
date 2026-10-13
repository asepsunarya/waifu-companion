"""Master's own yes, enforced in code, before anything turns the PC off.

Shutting the PC down, restarting it, putting it to sleep, or switching off the
plug that feeds it runs only after Master's next words say yes. The first such
call in a turn is refused and becomes that turn's request, and the brain ends its
reply with the gate's own question naming the action (`with_question`), so Master
always hears what a yes would run, even when the model forgets to ask. The same
call runs in the very next turn when Master's words as heard (`begin_turn`) are a
plain yes, said within the window. The model's own words never count, so a model
that skips the question, answers for Master, or mishears cannot turn the PC off.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from waifu.devices.base import Device
from waifu.devices.ir_device import resolve_command
from waifu.devices.registry import DeviceRegistry

logger = logging.getLogger(__name__)

# A yes starts with one of these ...
AFFIRMATIVE_WORDS = frozenset(
    {
        "ya",
        "iya",
        "iyaa",
        "yes",
        "yup",
        "yap",
        "boleh",
        "oke",
        "okey",
        "ok",
        "okay",
        "lanjut",
        "lanjutkan",
        "betul",
        "benar",
        "silakan",
        "setuju",
        "gas",
        "yakin",
        "yaudah",
        "siap",
        "sip",
        "yoi",
        "はい",
        "うん",
    }
)
# ... and holds nothing but more of them and these words, which only repeat the order or
# soften it. Thanks, another request, or a reason to wait ("oke makasih ya", "iya ntar",
# "ya kali, aku lagi main game") is therefore never a yes, whatever "ya" it holds.
ORDER_WORDS = frozenset(
    {
        "aja",
        "saja",
        "deh",
        "dong",
        "sekarang",
        "langsung",
        "udah",
        "sudah",
        "matikan",
        "matiin",
        "shutdown",
        "restart",
        "sleep",
        "tidurkan",
        "komputer",
        "komputernya",
        "pc",
        "nya",
        "colokan",
        "colokannya",
        "どうぞ",
        "お願いします",
    }
)
PC_COMMANDS_TO_CONFIRM = frozenset({"shutdown", "restart", "sleep"})
PLUG_COMMANDS_TO_CONFIRM = frozenset({"off", "toggle"})

# Runs of letters and digits in any script, so "はい" and "いいえ" are words too.
_WORD = re.compile(r"[^\W_]+")
# Always skipped in Master's answer, like her name and wake words.
_MASTER = "master"
# What the question says she is about to do, by the gated command.
_ACTIONS = {
    "shutdown": "kumatikan",
    "restart": "kunyalakan ulang",
    "sleep": "kutidurkan",
    "off": "kumatikan",
    "toggle": "kubalik",
}

# (device id, resolved command, value): what Master's yes has to answer.
_Call = tuple[str, str, int | None]


@dataclass(frozen=True)
class _Request:
    """A refused call and the question Master hears about it."""

    call: _Call
    at: float
    question: str


def is_yes(text: str, names: Iterable[str] = ()) -> bool:
    """Whether Master's words are a plain yes, such as "Iya, matikan." or "Cyrene, boleh".

    Leaving out "Master" and the `names` (her name and wake words she answers to, and what she
    calls the owner), the words must start with an affirmative word and hold only affirmative
    and order words. A question ("iya?") is never a yes.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    if "?" in text:
        return False
    skipped = {_MASTER, *(word for name in names for word in _WORD.findall(name.lower()))}
    words = [word for word in _WORD.findall(text) if word not in skipped]
    return (
        bool(words)
        and words[0] in AFFIRMATIVE_WORDS
        and all(word in AFFIRMATIVE_WORDS or word in ORDER_WORDS for word in words)
    )


def needs_confirmation(
    registry: DeviceRegistry, device_id: str, command: str, value: int | None
) -> bool:
    """Whether the resolved command shuts the PC down, restarts or sleeps it, or cuts its power.

    An unknown device or command needs none: `control_device` fails on it anyway.
    """
    return _gated(registry, device_id, command, value) is not None


class ConfirmationGate:
    """One request per turn, asked by the code and answered only by Master's next words.

    `names` are her name and wake words; `owner_call` is what she calls the owner. The question
    calls the owner by it, and like her names it is left out of the answer ("iya, Sayang").
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        window_s: float = 60,
        names: Iterable[str] = (),
        owner_call: str = "Master",
    ) -> None:
        self._clock = clock
        self._window_s = window_s
        self._names = (*names, owner_call)
        self._owner_call = owner_call
        self._lock = threading.Lock()  # guards the turn, its yes, and both requests
        self._turn_at = 0.0
        self._turn_is_yes = False
        self._asked: _Request | None = None  # the last turn's request, which this turn may answer
        self._recorded: _Request | None = None  # this turn's request, asked when the reply ends

    def begin_turn(self, user_text: str) -> None:
        """Start a turn with Master's words as heard; only these can confirm a request.

        The last turn's request becomes the one this turn may answer, and any older one expires.
        """
        yes = is_yes(user_text, self._names)
        now = self._clock()
        with self._lock:
            self._asked, self._recorded = self._recorded, None
            self._turn_at = now
            self._turn_is_yes = yes

    def awaiting_answer(self) -> bool:
        """Whether this turn may still answer the question asked in the turn before it."""
        with self._lock:
            return self._answerable()

    def check(
        self, registry: DeviceRegistry, device_id: str, command: str, value: int | None
    ) -> str | None:
        """None when the call may run now, otherwise the message telling the model why not.

        A gated call runs only when it is the last turn's request (same device, resolved command,
        and value), Master's words in this turn are a yes, and she said them at most `window_s`
        seconds after the refusal; that request is then used up. Any other gated call is refused,
        and the first one refused in a turn becomes its request. Calls that need no confirmation
        run and leave the requests alone.
        """
        gated = _gated(registry, device_id, command, value)
        if gated is None:
            return None
        device, resolved = gated
        call: _Call = (device_id, resolved, value)
        with self._lock:
            confirmed = (
                self._turn_is_yes
                and self._answerable()
                and self._asked is not None
                and self._asked.call == call
            )
            if confirmed:
                self._asked = None
            elif self._recorded is None:
                question = _question(device, resolved, self._owner_call)
                self._recorded = _Request(call, self._clock(), question)
            request = self._recorded
        if confirmed:
            logger.info("Master confirmed '%s' on '%s'", resolved, device_id)
            return None
        if request is not None and request.call == call:
            logger.info("Holding '%s' on '%s' until Master says yes", resolved, device_id)
            return _ask_first_message(device, resolved, request.question)
        logger.info("Holding '%s' on '%s'; another request is asked first", resolved, device_id)
        return _one_at_a_time_message(device, resolved)

    def with_question(self, reply: str) -> str:
        """The reply Master hears: `reply`, then the question for this turn's request, if any."""
        with self._lock:
            request = self._recorded
        if request is None:
            return reply
        return f"{reply} {request.question}" if reply else request.question

    def discard_turn(self) -> None:
        """Drop this turn's request: the turn was rolled back, so its question is never heard."""
        with self._lock:
            self._recorded = None

    def _answerable(self) -> bool:
        """The last turn's request is waiting and this turn's words came in time (lock held)."""
        return self._asked is not None and self._turn_at - self._asked.at <= self._window_s


def _gated(
    registry: DeviceRegistry, device_id: str, command: str, value: int | None
) -> tuple[Device, str] | None:
    """The device and its resolved command when the call needs Master's yes, else None."""
    device = registry.get(device_id)
    # Only the PC and its plug get past this, so no other device's commands (an IR device
    # reads its learned codes from disk) are read here.
    if device is None or not registry.powers_pc(device_id):
        return None
    resolved = resolve_command(command, value, device.commands())
    to_confirm = PC_COMMANDS_TO_CONFIRM if device.kind == "pc" else PLUG_COMMANDS_TO_CONFIRM
    return (device, resolved) if resolved in to_confirm else None


def _question(device: Device, command: str, owner_call: str) -> str:
    """The spoken question: it names the action and invites a yes the gate hears."""
    warning = "" if device.kind == "pc" else " Listrik PC bisa langsung putus."
    action = _ACTIONS[command]
    return f"{device.name} mau {action} sekarang, {owner_call}?{warning} Jawab iya kalau jadi."


def _ask_first_message(device: Device, command: str, question: str) -> str:
    return (
        f"Perintah '{command}' untuk {device.name} belum dijalankan: Master harus mengiyakannya "
        f'dulu. Setelah jawabanmu, pertanyaan ini otomatis dibacakan untuk Master: "{question}" '
        "Jangan menanyakannya lagi. Panggil perintah yang sama lagi hanya setelah Master "
        "menjawab ya."
    )


def _one_at_a_time_message(device: Device, command: str) -> str:
    return (
        f"Perintah '{command}' untuk {device.name} belum dijalankan dan tidak ditanyakan: hanya "
        "satu perintah seperti ini yang ditanyakan ke Master dalam satu giliran. Bilang ke Master "
        "bahwa yang ini perlu dia minta lagi nanti."
    )
