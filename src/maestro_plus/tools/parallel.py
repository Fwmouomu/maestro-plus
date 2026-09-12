"""Run many flows across many devices.

This is the tool the official server cannot offer, because parallel device
execution is Maestro Cloud's business model rather than an oversight. Doing it
locally is the reason this server is worth installing.

The returned numbers are measured, not estimated: wall-clock time for the batch
against the sum of the individual durations. A speedup figure that comes from a
real run is what turns "runs in parallel" from a claim into a result.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..backends import maestro_cli
from ..errors import MaestroPlusError, ToolError
from ..pool import POOL

logger = logging.getLogger(__name__)


def run_parallel(
    flows: Annotated[
        list[str],
        Field(
            min_length=1,
            description=(
                "Maestro flow files to run. Paths resolve against the server working directory."
            ),
        ),
    ],
    devices: Annotated[
        list[str] | None,
        Field(
            description="Preferred device serials, tried first in order. Omit to use whatever "
            "the pool has free. A preferred device that is busy is skipped rather than fatal."
        ),
    ] = None,
    env: Annotated[
        dict[str, str] | None,
        Field(description="Maestro environment variables, passed to every flow as -e KEY=VALUE."),
    ] = None,
    timeout: Annotated[
        int,
        Field(ge=10, le=7200, description="Per-flow timeout in seconds."),
    ] = 600,
) -> dict:
    """Run several Maestro flows in parallel across leased devices.

    Flows are distributed round-robin over the leased devices, so N flows on M
    devices take roughly N/M rounds. Every device is leased for the duration and
    released on exit, including on failure, so a crashed run cannot strand a
    device.

    Returns per-flow results plus wall-clock time, the sequential estimate, and
    the resulting speedup. A failure on one device never masks a pass on another:
    each result is reported separately.
    """
    flow_paths = [Path(flow).expanduser() for flow in flows]
    missing = [str(path) for path in flow_paths if not path.exists()]
    if missing:
        raise ToolError(
            f"Flow file(s) not found: {', '.join(missing)}. Paths resolve against the "
            "server process working directory, and relative paths are not resolved "
            "against the client's directory."
        )

    device_count = min(len(devices), len(flow_paths)) if devices else len(flow_paths)

    started = time.monotonic()
    results: list[maestro_cli.FlowResult] = []
    used_devices: list[str] = []

    with POOL.lease(count=device_count, preferred=devices) as lease:
        used_devices = list(lease.devices)
        assignments = [
            (flow, used_devices[index % len(used_devices)])
            for index, flow in enumerate(flow_paths)
        ]
        logger.info(
            "running %d flow(s) across %d device(s): %s",
            len(assignments),
            len(used_devices),
            used_devices,
        )

        slots: list[maestro_cli.FlowResult | None] = [None] * len(assignments)

        with ThreadPoolExecutor(max_workers=len(used_devices)) as executor:
            pending: dict[object, int] = {}
            for index, (flow, serial) in enumerate(assignments):
                future = executor.submit(
                    maestro_cli.run_flow,
                    flow,
                    device=serial,
                    env=env,
                    timeout=timeout,
                )
                pending[future] = index

            for future in as_completed(pending):
                index = pending[future]
                flow, serial = assignments[index]
                try:
                    slots[index] = future.result()
                except MaestroPlusError as exc:
                    logger.warning("flow %s on %s could not run: %s", flow.name, serial, exc)
                    slots[index] = maestro_cli.FlowResult(
                        flow=flow.name,
                        device=serial,
                        success=False,
                        exit_code=-1,
                        duration_s=0.0,
                        stderr_tail=str(exc),
                    )

        results = [slot for slot in slots if slot is not None]
        wall_time = time.monotonic() - started

    summary = maestro_cli.flatten(results)
    sequential = sum(result.duration_s for result in results)

    return {
        **summary,
        "devices_used": used_devices,
        "wall_time_s": round(wall_time, 2),
        "sequential_estimate_s": round(sequential, 2),
        "speedup": round(sequential / wall_time, 2) if wall_time > 0 else None,
    }


__all__ = ["run_parallel"]
