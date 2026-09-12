"""Direct adb access.

Everything here shells out to the ``adb`` binary. Nothing in this module imports
the official MCP server, which is the whole point: these functions keep working
when the official server is missing, crashed, or speaking a protocol version we
do not understand.

Call cost matters here. ``dump_hierarchy`` burns roughly a second per call on a
mid-range device, so it is the recovery path rather than the default read.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from ..errors import DeviceError, MaestroPlusError, ToolchainError

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
HIERARCHY_REMOTE_PATH = "/sdcard/maestro_plus_dump.xml"


def _adb_binary() -> str:
    found = shutil.which("adb")
    if found is None:
        raise ToolchainError(
            "adb was not found on PATH. Install Android platform-tools and confirm "
            "`adb version` works in a terminal, then re-run health_check."
        )
    return found


def run(
    *args: str,
    serial: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    check: bool = True,
) -> str:
    """Run an adb command against an optional device and return stdout as text.

    Text decoding is lenient on purpose: device output carries vendor-specific
    bytes often enough that strict UTF-8 would turn a cosmetic issue into a
    crash.
    """
    cmd = [_adb_binary()]
    if serial:
        cmd += ["-s", serial]
    cmd += [str(a) for a in args]

    logger.debug("adb %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MaestroPlusError(
            f"adb timed out after {timeout}s: {' '.join(args)}. "
            "The device may be wedged; try `adb reconnect`."
        ) from exc

    if check and proc.returncode != 0:
        raise MaestroPlusError(
            f"adb failed with exit code {proc.returncode}: {' '.join(args)}\n"
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


@dataclass(frozen=True)
class Device:
    """One entry from ``adb devices -l``."""

    serial: str
    state: str
    props: dict[str, str] = field(default_factory=dict)

    @property
    def is_usable(self) -> bool:
        return self.state == "device"

    @property
    def model(self) -> str:
        return self.props.get("model", "unknown")

    @property
    def product(self) -> str:
        return self.props.get("product", "unknown")

    @property
    def transport_id(self) -> str | None:
        return self.props.get("transport_id")

    @property
    def is_emulator(self) -> bool:
        return self.serial.startswith("emulator-") or self.model.startswith(
            ("sdk_", "google_sdk", "Android_SDK")
        )

    def android_version(self, timeout: int = 10) -> str | None:
        """Read the release version. Returns None when the device is unreachable."""
        try:
            value = run(
                "shell", "getprop", "ro.build.version.release", serial=self.serial, timeout=timeout
            ).strip()
        except (MaestroPlusError, ToolchainError) as exc:
            logger.debug("could not read android version for %s: %s", self.serial, exc)
            return None
        return value or None

    def to_dict(self) -> dict[str, object]:
        return {
            "serial": self.serial,
            "state": self.state,
            "model": self.model,
            "product": self.product,
            "is_emulator": self.is_emulator,
            "is_usable": self.is_usable,
            "android_version": self.android_version() if self.is_usable else None,
        }


def list_devices() -> list[Device]:
    """Parse ``adb devices -l``.

    The first line of real output is a header, and the daemon sometimes prefixes
    a ``* daemon started successfully *`` notice, so both are skipped.
    """
    output = run("devices", "-l")
    devices: list[Device] = []

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("List of devices", "*")):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue

        serial, state = parts[0], parts[1]
        props: dict[str, str] = {}
        for token in parts[2:]:
            key, separator, value = token.partition(":")
            if separator:
                props[key] = value

        devices.append(Device(serial=serial, state=state, props=props))

    return devices


def require_device(serial: str) -> Device:
    """Return a usable device or raise, with the reason spelled out."""
    devices = list_devices()
    for device in devices:
        if device.serial != serial:
            continue
        if not device.is_usable:
            raise DeviceError(
                f"Device {serial} is present but in state {device.state!r}, so it cannot run a "
                "flow. For 'unauthorized', unlock the screen and accept the USB debugging "
                "prompt. For 'offline', try `adb reconnect` or restart the emulator."
            )
        return device
    available = ", ".join(d.serial for d in devices) or "none"
    raise DeviceError(f"Device {serial} is not connected. Currently visible: {available}.")


def dump_hierarchy(serial: str, timeout: int = 60) -> ElementTree.Element:
    """Read the current view hierarchy through uiautomator.

    Writes to the device first, then reads back, because ``exec-out`` interleaves
    the tool's own progress message with the XML on some OEM builds.
    """
    run("shell", "uiautomator", "dump", HIERARCHY_REMOTE_PATH, serial=serial, timeout=timeout)
    raw = run("shell", "cat", HIERARCHY_REMOTE_PATH, serial=serial, timeout=timeout)

    start = raw.find("<?xml")
    if start == -1:
        start = raw.find("<hierarchy")
    if start == -1:
        raise MaestroPlusError(
            "uiautomator dump returned no XML. This usually means the app window was "
            f"mid-animation or the screen was off. First 200 characters: {raw[:200]!r}"
        )
    try:
        return ElementTree.fromstring(raw[start:])
    except ElementTree.ParseError as exc:
        raise MaestroPlusError(f"uiautomator dump returned malformed XML: {exc}") from exc


def screenshot(serial: str, destination: Path, timeout: int = 30) -> Path:
    """Capture the screen straight into a local file.

    Binary mode, and ``exec-out`` rather than ``pull``, so the PNG is never
    written to the device and never passes through newline translation — either
    of which would produce a file that decodes as garbage.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [_adb_binary(), "-s", serial, "exec-out", "screencap", "-p"]

    logger.debug("adb screencap -> %s", destination)
    try:
        with destination.open("wb") as handle:
            proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MaestroPlusError(f"Screen capture from {serial} timed out after {timeout}s") from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MaestroPlusError(f"Screen capture from {serial} failed: {stderr}")
    if destination.stat().st_size == 0:
        raise MaestroPlusError(
            f"Screen capture from {serial} produced an empty file. "
            "The screen is probably off or the device is showing a secure window."
        )
    return destination


def recent_logs(
    serial: str,
    lines: int = 400,
    package: str | None = None,
    timeout: int = 30,
) -> str:
    """Dump the tail of logcat, optionally narrowed to one package.

    ``-d`` takes the current buffer and exits instead of following, which is what
    a diagnosis pass wants: a fixed snapshot of what happened, not a stream that
    keeps the call open.
    """
    args = ["logcat", "-d", "-t", str(lines), "-v", "threadtime"]

    if package:
        pid = _pid_of(serial, package)
        if pid is None:
            logger.debug(
                "package %s is not running on %s; returning the unfiltered buffer",
                package,
                serial,
            )
        else:
            args += ["--pid", str(pid)]

    return run(*args, serial=serial, timeout=timeout, check=False)


def _pid_of(serial: str, package: str, timeout: int = 10) -> int | None:
    """Resolve a package name to a running pid, or None when it is not running."""
    try:
        output = run("shell", "pidof", package, serial=serial, timeout=timeout, check=False)
    except (MaestroPlusError, ToolchainError):
        return None
    first = output.strip().split()[0] if output.strip() else ""
    return int(first) if first.isdigit() else None


def current_focus(serial: str, timeout: int = 10) -> str | None:
    """Return the currently focused window, e.g. ``com.example/.MainActivity``.

    Used by diagnosis to notice when the app under test is no longer in front —
    an ANR dialog or a system permission sheet will silently invalidate every
    assertion that follows.
    """
    try:
        dump = run("shell", "dumpsys", "window", serial=serial, timeout=timeout, check=False)
    except (MaestroPlusError, ToolchainError):
        return None

    for line in dump.splitlines():
        stripped = line.strip()
        if stripped.startswith(("mCurrentFocus", "mFocusedApp")):
            _, _, value = stripped.partition("=")
            return value.strip().rstrip("}").strip()
    return None


def is_available() -> bool:
    """True when adb is installed and its daemon answers."""
    if shutil.which("adb") is None:
        return False
    try:
        run("version", timeout=10)
    except (MaestroPlusError, ToolchainError):
        return False
    return True


__all__ = [
    "Device",
    "current_focus",
    "dump_hierarchy",
    "is_available",
    "list_devices",
    "recent_logs",
    "require_device",
    "run",
    "screenshot",
]
