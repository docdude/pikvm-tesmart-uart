import asyncio
import json

from typing import Any
from typing import Callable
from typing import Final

from ... import aiotools
from ... import tools

from ...logging import get_logger

from ...yamlconf import Option
from ...yamlconf import Section

from ...validators import check_string_in_list
from ...validators.basic import valid_float_f01
from ...validators.os import valid_abs_path

from . import BaseUserGpioDriver
from . import GpioDriverOfflineError


# Upper bounds; each unit's generated UI uses a subset of these pins.
_MAX_PCS: Final[int] = 16
_OUTPUTS: Final[str] = "abcd"

# Mutually exclusive pins: pin -> (state key path, selected value, daemon command).
_CHOICE_PINS: Final[dict[str, tuple[str, Any, str]]] = {
    **{f"pc{n}": ("active_pc", n, f"select {n}") for n in range(1, _MAX_PCS + 1)},
    **{
        f"out_{o}_pc{n}": (f"outputs.{o.upper()}", n, f"select {o} {n}")
        for o in _OUTPUTS
        for n in range(1, _MAX_PCS + 1)
    },
    **{
        f"buzzer_{m}": ("buzzer", m, f"set buzzer {m}")
        for m in ("off", "low", "medium", "high")
    },
    **{
        f"lighting_{m}": ("lighting", m, f"set lighting {m}")
        for m in ("off", "indicator", "marquee", "breathing")
    },
    **{
        f"fan_{m}": ("fan", m, f"set fan {m}")
        for m in ("off", "auto", "low", "high")
    },
    **{f"audio_pc{n}": ("audio_pc", n, f"set audio pc{n}") for n in range(1, _MAX_PCS + 1)},
    **{
        f"mixed_pc{n}": ("mixed_focus_pc", n, f"set mixed_focus pc{n}")
        for n in range(1, _MAX_PCS + 1)
    },
    **{
        f"km_{m}": ("km_mode", m, f"set km_mode {m}")
        for m in ("passthrough", "compatible")
    },
    **{
        f"scan_{s}": ("scan_interval", s, f"set scan_interval {s}")
        for s in (5, 10, 15, 30, 60)
    },
}

# On/off pins: pin -> daemon setting name.
_BOOL_PINS: Final[dict[str, str]] = {
    "autoscan": "autoscan",
    "buzzer": "buzzer",
    "mouse": "mouse",
    "audio_follow": "audio_follow",
    "auto_detect": "auto_detect",
    **{f"net_pc{n}": f"network_pc{n}" for n in range(1, 9)},
}

# Momentary pins for pulse buttons: pin -> daemon command.
_PULSE_PINS: Final[dict[str, str]] = {
    "pc_next": "select next",
    **{f"out_{o}_next": f"select {o} next" for o in _OUTPUTS},
    "audio_next": "set audio next",
    "mixed_next": "set mixed_focus next",
    "scan_next": "set scan_interval next",
}

# Read-only pins for mode: input: pin -> (state key, value).
_INPUT_PINS: Final[dict[str, tuple[str, Any]]] = {
    f"net_focus_pc{n}": ("network_focus_pc", n) for n in range(1, 9)
}

_ALL_PINS: Final[tuple[str, ...]] = (*_CHOICE_PINS, *_BOOL_PINS, *_INPUT_PINS, *_PULSE_PINS)


def _valid_pin(arg: Any) -> str:
    return check_string_in_list(arg, "TESmart pin", _ALL_PINS)


def _get(state: dict, path: str) -> Any:
    value: Any = state
    for key in path.split("."):
        value = (value or {}).get(key)
    return value


def _read_pin(state: dict, pin: str) -> bool:
    if pin in _PULSE_PINS:
        return False
    if pin in _CHOICE_PINS:
        (key, value, _) = _CHOICE_PINS[pin]
        return (_get(state, key) == value)
    if pin in _INPUT_PINS:
        (key, value) = _INPUT_PINS[pin]
        return (state.get(key) == value)
    if pin.startswith("net_"):
        return bool((state.get("network_ports") or {}).get(pin[4:]))
    return (state.get(_BOOL_PINS[pin]) == "on")


class Plugin(BaseUserGpioDriver):

    def __init__(
        self,
        instance_name: str,
        notifier: aiotools.AioNotifier,
        c: Section,
    ) -> None:

        super().__init__(instance_name, notifier, c)

        self.__socket_path: Final[str] = c.socket
        self.__timeout: Final[float] = c.timeout
        self.__state_poll: Final[float] = c.state_poll

        self.__state: dict = {}
        self.__online: bool = False
        self.__update_notifier = aiotools.AioNotifier()
        self.__pins: set[str] = set()

    @classmethod
    def get_plugin_options(cls) -> dict:
        return {
            "socket": Option(
                "/run/tesmartd.sock",
                type=valid_abs_path,
            ),
            "timeout": Option(
                1.0,
                type=valid_float_f01,
            ),
            "state_poll": Option(
                0.25,
                type=valid_float_f01,
            ),
        }

    @classmethod
    def get_pin_validator(cls) -> Callable[[Any], Any]:
        return _valid_pin

    def register_input(self, pin: str, debounce: float) -> None:
        _ = debounce
        self.__pins.add(pin)

    def register_output(self, pin: str, initial: (bool | None)) -> None:
        _ = initial
        self.__pins.add(pin)

    async def run(self) -> None:
        prev: tuple | None = None

        while True:
            try:
                self.__state = (await self.__command("state"))["state"]
                self.__online = True

            except Exception as ex:
                if self.__online:
                    get_logger(0).error(
                        "Lost connection to tesmartd: %s",
                        tools.efmt(ex),
                    )

                self.__state = {}
                self.__online = False

            snapshot = (
                self.__online,
                *(_read_pin(self.__state, pin) for pin in sorted(self.__pins)),
            )
            if snapshot != prev:
                self._notifier.notify()
                prev = snapshot

            await self.__update_notifier.wait(self.__state_poll)

    async def cleanup(self) -> None:
        self.__online = False
        self.__state = {}

    async def read(self, pin: str) -> bool:
        if not self.__online:
            raise GpioDriverOfflineError(self)
        return _read_pin(self.__state, pin)

    async def write(self, pin: str, state: bool) -> None:
        if pin in _PULSE_PINS:
            # Pulse sends ON then OFF; act once on the rising edge.
            if not state:
                return
            command = _PULSE_PINS[pin]
        elif pin in _CHOICE_PINS:
            # Choices only act on the ON transition; turning the active
            # choice OFF is ignored and the UI reverts on the next read.
            if not state:
                return
            command = _CHOICE_PINS[pin][2]
        else:
            command = f"set {_BOOL_PINS[pin]} {'on' if state else 'off'}"

        try:
            await self.__command(command)
        except Exception as ex:
            get_logger(0).error(
                "Can't apply TESmart %r: %s",
                command,
                tools.efmt(ex),
            )
            self.__online = False
            self._notifier.notify()
            raise GpioDriverOfflineError(self)

        # Wake the polling loop instead of waiting for state_poll.
        self.__update_notifier.notify()

    async def __command(self, command: str) -> dict:
        writer = None

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.__socket_path),
                timeout=self.__timeout,
            )

            writer.write((command + "\n").encode())
            await asyncio.wait_for(
                writer.drain(),
                timeout=self.__timeout,
            )

            raw = await asyncio.wait_for(
                reader.readline(),
                timeout=self.__timeout,
            )

            if not raw:
                raise RuntimeError("Empty response from tesmartd")

            response = json.loads(raw.decode())

            if not response.get("ok"):
                raise RuntimeError(
                    response.get("error", "tesmartd command failed")
                )

            return response

        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    def __str__(self) -> str:
        return f"TESmart({self._instance_name})"

    __repr__ = __str__
