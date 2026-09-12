"""In-process device leasing.

Concurrency needs a single owner per device. Two agents driving one emulator
produce a failure that looks like an application defect and is not — the classic
way parallel mobile testing wastes a day.

Scope is one process, on purpose. Two server instances on the same machine do not
see each other's leases. That is documented as a limitation rather than papered
over with a lock file, because a stale lock file from a crashed run is a worse
failure mode than an honest boundary.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from .backends import adb
from .errors import DeviceError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Lease:
    """A reservation of one or more devices for the duration of a block."""

    owner: str
    devices: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.devices)


class DevicePool:
    """Tracks which devices are in use by in-flight runs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leases: dict[str, str] = {}

    def leased(self) -> dict[str, str]:
        """Map of serial to lease owner."""
        with self._lock:
            return dict(self._leases)

    def snapshot(self) -> list[dict[str, object]]:
        """Every connected device with its health, lease state, and metadata.

        Sorted so the useful rows come first: usable and free, then usable and
        busy, then the broken ones. An agent reading a truncated list should see
        something it can act on.
        """
        leases = self.leased()
        rows: list[dict[str, object]] = []

        for device in adb.list_devices():
            row = device.to_dict()
            row["leased_by"] = leases.get(device.serial)
            rows.append(row)

        rows.sort(
            key=lambda row: (
                not row["is_usable"],
                bool(row["leased_by"]),
                str(row["serial"]),
            )
        )
        return rows

    def free_serials(self) -> list[str]:
        """Serials of usable devices that nobody holds."""
        leases = self.leased()
        return [
            device.serial
            for device in adb.list_devices()
            if device.is_usable and device.serial not in leases
        ]

    @contextmanager
    def lease(self, count: int = 1, preferred: Sequence[str] | None = None) -> Iterator[Lease]:
        """Reserve ``count`` usable devices until the block exits.

        Devices named in ``preferred`` are taken first, but a preference is not a
        requirement: if one of them is missing or busy, the pool falls back to
        whatever is free. Refusing to run because the caller's first-choice
        emulator is gone would make this tool useless in CI, where device
        identity is not stable.
        """
        if count < 1:
            raise ValueError("count must be at least 1")

        usable = [device for device in adb.list_devices() if device.is_usable]
        if preferred:
            rank = {serial: index for index, serial in enumerate(preferred)}
            usable.sort(key=lambda device: (rank.get(device.serial, len(rank)), device.serial))

        with self._lock:
            chosen = [device for device in usable if device.serial not in self._leases][:count]
            if len(chosen) < count:
                busy = sorted(self._leases)
                raise DeviceError(
                    f"Asked for {count} device(s) but only {len(chosen)} are free. "
                    f"Connected and usable: {len(usable)}. "
                    f"Currently leased: {busy or 'none'}."
                )
            owner = uuid.uuid4().hex[:8]
            for device in chosen:
                self._leases[device.serial] = owner

        serials = [device.serial for device in chosen]
        logger.info("leased %s to %s", serials, owner)

        try:
            yield Lease(owner=owner, devices=tuple(serials))
        finally:
            with self._lock:
                released = [
                    serial for serial in serials if self._leases.get(serial) == owner
                ]
                for serial in released:
                    self._leases.pop(serial, None)
            logger.info("released %s from %s", released, owner)


POOL = DevicePool()

__all__ = ["POOL", "DevicePool", "Lease"]
