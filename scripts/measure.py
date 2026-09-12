#!/usr/bin/env python
"""Measure the numbers the README's evaluation table is waiting for.

Three claims in that table are marked `not yet measured`. This script exists so
they do not stay that way. Point it at a real suite on real devices and it prints
finished markdown rows, ready to paste over the placeholders.

Two of the three are mechanical: wall time, and how much evidence de-duplication
actually removes. The third — whether `debug_failure` attributed a failure
correctly — is not automatable, because deciding whether a diagnosis was right
requires someone who knows the real root cause. Rather than invent an accuracy
figure, the script prints the labelling template that produces one honestly.

Usage:
    python scripts/measure.py --flows flows/ --devices emulator-5554,emulator-5556
    python scripts/measure.py --flows flows/ --devices emulator-5554 --artifacts ~/.maestro-plus/artifacts

The `sys.path` insertion below is deliberate: this is a measurement tool, not a
library, and it should run from a checkout without `pip install` first.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from maestro_plus.backends import maestro_cli  # noqa: E402

SEPARATOR = "-" * 72


def collect_flows(source: Path) -> list[Path]:
    """Every Maestro flow under a directory, or the single file that was named."""
    if source.is_file():
        return [source]

    flows = sorted({*source.rglob("*.yaml"), *source.rglob("*.yml")})
    if not flows:
        raise SystemExit(f"No Maestro flows found under {source}")
    return flows


def run_sequential(
    flows: list[Path], device: str, timeout: int
) -> tuple[float, list[maestro_cli.FlowResult]]:
    """One device, one flow at a time. The baseline every speedup is measured against."""
    started = time.monotonic()
    results = [maestro_cli.run_flow(flow, device=device, timeout=timeout) for flow in flows]
    return time.monotonic() - started, results


def run_parallel(
    flows: list[Path], devices: list[str], timeout: int
) -> tuple[float, list[maestro_cli.FlowResult]]:
    """Round-robin across devices, collecting results in the original flow order.

    The order preservation matters: results come back in completion order, and a
    table where row three belongs to a different flow than row three's name is
    worse than no table.
    """
    assignments = [
        (index, flow, devices[index % len(devices)]) for index, flow in enumerate(flows)
    ]
    slots: list[maestro_cli.FlowResult | None] = [None] * len(flows)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        futures = {
            pool.submit(maestro_cli.run_flow, flow, device=serial, timeout=timeout): index
            for index, flow, serial in assignments
        }
        for future in as_completed(futures):
            slots[futures[future]] = future.result()
    return time.monotonic() - started, [result for result in slots if result is not None]


def dedupe_artifacts(directory: Path) -> tuple[int, int]:
    """How many screenshots exist versus how many distinct states they represent."""
    from maestro_plus import evidence

    screenshots = sorted(directory.rglob("*.png"))
    if not screenshots:
        return 0, 0

    frames = evidence.dedupe_screenshots(screenshots)
    return len(screenshots), len(evidence.unique_paths(frames))


def speedup_row(flow_count: int, device_count: int, sequential: float, parallel: float) -> str:
    ratio = sequential / parallel if parallel > 0 else 0.0
    return (
        f"| Parallel execution reduces wall time | {flow_count} flows across "
        f"{device_count} device(s) | **{ratio:.2f}x** ({sequential:.0f}s sequential "
        f"vs {parallel:.0f}s parallel) |"
    )


def dedupe_row(total: int, kept: int) -> str:
    if not total:
        return (
            "| Screenshot de-duplication compresses evidence | "
            "Artifact count before and after de-duplication | Not yet measured |"
        )
    removed = total - kept
    return (
        f"| Screenshot de-duplication compresses evidence | {total} captured PNGs in one "
        f"artifact tree | **{removed}/{total} dropped ({removed / total:.0%})** |"
    )


ATTRIBUTION_TEMPLATE = """\
The remaining row needs a human, and says so.

For each failed run you collect, record the three fields below. Ten is a start;
thirty means something. Keep the labels honest — an entry where you guessed the
root cause poisons the number you are trying to produce.

  flow           | real cause (application / test / environment) | diagnosed | confidence
  ---------------|----------------------------------------------|-----------|-----------
  login.yaml     |                                              |           |

Then: accuracy = (diagnosed == real cause) / total, and separately report how
often the tool said `unknown`. A tool that is right 60% of the time and admits
the other 40% is more useful than one that claims 95% — so report both.
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the claims in the README evaluation table.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--flows", required=True, help="Directory of Maestro flows, or one file")
    parser.add_argument("--devices", required=True, help="Comma-separated device serials")
    parser.add_argument("--timeout", type=int, default=600, help="Per-flow timeout in seconds")
    parser.add_argument(
        "--artifacts",
        help="Artifact tree to measure screenshot de-duplication against",
    )
    args = parser.parse_args()

    devices = [serial.strip() for serial in args.devices.split(",") if serial.strip()]
    if not devices:
        raise SystemExit("At least one device serial is required")

    flows = collect_flows(Path(args.flows).expanduser())
    print(f"Measuring {len(flows)} flow(s) across {len(devices)} device(s).")
    print("This takes as long as your suite takes, twice. Run it when that is acceptable.\n")

    sequential_wall, sequential_results = run_sequential(flows, devices[0], args.timeout)
    parallel_wall, parallel_results = run_parallel(flows, devices, args.timeout)

    sequential_failed = sum(1 for result in sequential_results if not result.success)
    parallel_failed = sum(1 for result in parallel_results if not result.success)

    total, kept = (0, 0)
    if args.artifacts:
        total, kept = dedupe_artifacts(Path(args.artifacts).expanduser())

    print(SEPARATOR)
    print("RESULTS")
    print(SEPARATOR)
    print(f"Sequential : {sequential_wall:7.1f}s  ({sequential_failed} failed)")
    print(f"Parallel   : {parallel_wall:7.1f}s  ({parallel_failed} failed)")
    if total:
        print(f"Screenshots: {total} captured, {kept} distinct states")
    print()

    if sequential_failed != parallel_failed:
        print(
            "Warning: the two runs did not fail the same number of flows. A speedup measured "
            "across runs with different outcomes compares different amounts of work — "
            "re-run on a stable suite before publishing the number.\n"
        )

    print(SEPARATOR)
    print("MARKDOWN ROWS FOR THE README")
    print(SEPARATOR)
    print(speedup_row(len(flows), len(devices), sequential_wall, parallel_wall))
    print(dedupe_row(total, kept))
    print("| `debug_failure` correctly attributes the defect | Hand-labelled set of failures "
          "with a known root cause | See template below |")
    print()
    print(ATTRIBUTION_TEMPLATE)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
