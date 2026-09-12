"""Device inventory and toolchain self-check.

These two tools are the ones an agent should call before anything else, because
every other tool in this server can fail for reasons that live here: a missing
binary, an unauthorized device, a wedged emulator. Reporting that precisely is
worth more than any assertion helper.
"""

from __future__ import annotations

import logging

from ..backends import adb, maestro_cli
from ..errors import MaestroPlusError
from ..pool import POOL

logger = logging.getLogger(__name__)


def _pillow_available() -> bool:
    """Whether Pillow is importable, needed for every visual comparison."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def list_device_pool() -> dict:
    """List every connected Android device and emulator with its health and lease state.

    Call this before planning any parallel work: it is the only way to learn how
    many devices are actually usable right now, and which of them are already
    held by runs in flight. A device is `usable` when adb reports state `device`;
    `unauthorized` and `offline` devices appear in the list but cannot run flows.
    """
    devices = POOL.snapshot()
    usable = [device for device in devices if device["is_usable"]]
    free = [device for device in usable if not device["leased_by"]]

    return {
        "total": len(devices),
        "usable": len(usable),
        "free": len(free),
        "free_serials": [device["serial"] for device in free],
        "devices": devices,
    }


def health_check() -> dict:
    """Verify the toolchain: which tools work, which are impaired, what to install.

    Run this first when anything behaves unexpectedly. It distinguishes "the
    Maestro CLI is missing" from "the CLI is fine but the emulator is
    unauthorized", which are two very different afternoons.
    """
    adb_available = adb.is_available()
    maestro_version = maestro_cli.version()
    maestro_available = maestro_version is not None
    pillow = _pillow_available()

    devices: list[dict] = []
    device_error: str | None = None
    if adb_available:
        try:
            devices = [device.to_dict() for device in adb.list_devices()]
        except MaestroPlusError as exc:
            device_error = str(exc)
            logger.warning("device enumeration failed: %s", exc)

    usable_devices = [device for device in devices if device["is_usable"]]
    has_device = bool(usable_devices)

    capabilities = {
        "list_device_pool": adb_available,
        "health_check": True,
        "run_parallel": adb_available and maestro_available and has_device,
        "run_and_assert": maestro_available and adb_available,
        "assert_visual": pillow,
        "debug_failure": adb_available or maestro_available,
        "explore_and_record": maestro_available and adb_available,
    }

    recommendations: list[str] = []
    if not adb_available:
        recommendations.append(
            "Install Android platform-tools and put `adb` on PATH. Device discovery, "
            "screenshot capture, and the offline fallback path all depend on it."
        )
    if not maestro_available:
        recommendations.append(
            'Install the Maestro CLI: curl -Ls "https://get.maestro.mobile.dev" | bash. '
            "Without it, no tool that runs a flow can work."
        )
    if not pillow:
        recommendations.append(
            "Install Pillow (`pip install Pillow`) to enable assert_visual and "
            "screenshot de-duplication. Everything else works without it."
        )
    if adb_available and not devices:
        recommendations.append(
            "adb reports no connected devices. Start an emulator or plug in a device, "
            "then confirm with `adb devices`."
        )
    if devices and not usable_devices:
        states = sorted({str(device["state"]) for device in devices})
        recommendations.append(
            f"Devices are visible but none are usable (states: {', '.join(states)}). "
            "For 'unauthorized', accept the USB debugging prompt on the device. "
            "For 'offline', run `adb reconnect`."
        )

    return {
        "ok": all(capabilities[key] for key in ("list_device_pool", "run_parallel")),
        "tools": {
            "adb": {"available": adb_available},
            "maestro": {"available": maestro_available, "version": maestro_version},
            "pillow": {"available": pillow},
        },
        "devices": {
            "count": len(devices),
            "usable": len(usable_devices),
            "detail": devices,
            "error": device_error,
        },
        "capabilities": capabilities,
        "recommendations": recommendations,
    }


__all__ = ["health_check", "list_device_pool"]
