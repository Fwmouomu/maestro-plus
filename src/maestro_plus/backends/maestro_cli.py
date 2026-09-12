"""Wrapper around the Maestro command line.

The official MCP server wraps this same binary. Going straight to the CLI means
one process instead of two and one protocol hop instead of two, which matters
because every layer between an agent and a device is another layer that can be
wrong about why something failed.

Version sensitivity is handled deliberately. The exit code decides pass or fail
because it is the one signal every Maestro release honours. Report formats are
treated as a bonus: if ``--format junit`` is unsupported on the installed
version, the run still produces a usable result instead of crashing.

Costs roughly one JVM-class startup per invocation, so callers that run many
flows should go through :func:`run_many` rather than looping over
:func:`run_flow`.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from ..errors import FlowError, MaestroPlusError, ToolchainError

logger = logging.getLogger(__name__)

DEFAULT_FLOW_TIMEOUT = 600
DEFAULT_TAIL_LINES = 40
MAESTRO_OUTPUT_ROOT = Path.home() / ".maestro" / "tests"

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
_FAIL_LINE_RE = re.compile(
    r"(^\s*\[Failed\]|^\s*FAILED\b|^\s*FAIL\b)", re.IGNORECASE | re.MULTILINE
)


def _maestro_binary() -> str:
    found = shutil.which("maestro")
    if found is None:
        raise ToolchainError(
            "The Maestro CLI was not found on PATH. Install it with "
            '`curl -Ls "https://get.maestro.mobile.dev" | bash`, then re-run health_check.'
        )
    return found


def version(timeout: int = 30) -> str | None:
    """Return the installed Maestro version, or None when it cannot be read.

    Kept non-raising so callers can use it as a probe.
    """
    if shutil.which("maestro") is None:
        return None
    try:
        raw = subprocess.run(
            [_maestro_binary(), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("maestro --version failed: %s", exc)
        return None

    match = _VERSION_RE.search(raw.stdout or raw.stderr)
    return match.group(0) if match else None


def is_available() -> bool:
    return version() is not None


def _tail(text: str, lines: int = DEFAULT_TAIL_LINES) -> str:
    if not text:
        return ""
    parts = text.splitlines()
    if len(parts) <= lines:
        return text
    return "\n".join(["... (truncated)", *parts[-lines:]])


def _decoded(stream: bytes | str | None) -> str:
    """What TimeoutExpired carries depends on the Python version: bytes or str."""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", "replace")
    return stream or ""


def parse_junit(path: Path) -> list[str]:
    """Return the names of failed test cases in a JUnit report.

    Returns an empty list rather than raising: a missing or unfamiliar report is
    a diagnostic downgrade, never a reason to lose the run result.
    """
    try:
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError) as exc:
        logger.debug("could not parse junit report %s: %s", path, exc)
        return []

    failed: list[str] = []
    for case in tree.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(case.get("name") or "unnamed test case")
    return failed


def parse_failed_steps(stdout: str) -> list[str]:
    """Best-effort extraction of failing steps from human-readable output.

    Used only when no JUnit report is available. Deliberately shallow: a deep
    parse of console output would break on every Maestro release.
    """
    steps: list[str] = []
    for line in stdout.splitlines():
        if _FAIL_LINE_RE.search(line):
            cleaned = line.strip().lstrip("-* ").strip()
            if cleaned and cleaned not in steps:
                steps.append(cleaned)
    return steps


def find_latest_junit(since: float, root: Path | None = None) -> Path | None:
    """Find the JUnit report Maestro wrote during the run that just finished.

    Maestro stores reports under ``~/.maestro/tests/<timestamp>/`` without being
    asked, which makes reading the newest one strictly safer than requesting one
    on the command line. ``since`` is a wall-clock cutoff so a report left over
    from a previous run cannot be mistaken for this one's.
    """
    search_root = root or MAESTRO_OUTPUT_ROOT
    if not search_root.is_dir():
        return None

    candidates: list[Path] = []
    for path in search_root.rglob("*.xml"):
        try:
            if path.stat().st_mtime >= since:
                candidates.append(path)
        except OSError:
            continue

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


@dataclass
class FlowResult:
    """The outcome of one flow on one device."""

    flow: str
    device: str | None
    success: bool
    exit_code: int
    duration_s: float
    junit_path: Path | None = None
    failed_steps: list[str] = field(default_factory=list)
    stdout_tail: str = ""
    stderr_tail: str = ""
    timed_out: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "flow": self.flow,
            "device": self.device,
            "success": self.success,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 2),
            "timed_out": self.timed_out,
            "failed_steps": self.failed_steps,
            "junit_path": str(self.junit_path) if self.junit_path else None,
        }

    def summary_line(self) -> str:
        status = "PASS" if self.success else ("TIMEOUT" if self.timed_out else "FAIL")
        device = self.device or "default device"
        return f"[{status}] {self.flow} on {device} ({self.duration_s:.1f}s)"


def build_command(
    flow: Path,
    *,
    device: str | None = None,
    env: dict[str, str] | None = None,
    junit_path: Path | None = None,
) -> list[str]:
    """Assemble the argv for one flow run.

    Options precede the positional flow path because that ordering is accepted
    across the 2.x line, while trailing options are not.
    """
    argv = [_maestro_binary(), "test"]
    if device:
        argv += ["--device", device]
    if junit_path:
        argv += ["--format", "junit", "--output", str(junit_path)]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    argv.append(str(flow))
    return argv


def run_flow(
    flow: str | Path,
    *,
    device: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_FLOW_TIMEOUT,
) -> FlowResult:
    """Run a single Maestro flow and report what happened.

    The exit code decides success. Everything else — the JUnit report, the
    extracted failing steps — is enrichment, and it degrades quietly rather than
    costing us the run.
    """
    flow_path = Path(flow).expanduser()
    if not flow_path.exists():
        raise FlowError(
            f"Flow file not found: {flow_path}. Paths are resolved relative to the server "
            "process working directory."
        )

    argv = build_command(flow_path, device=device, env=env)
    logger.info("running flow %s on %s", flow_path.name, device or "default device")

    started = time.monotonic()
    started_wall = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _decoded(exc.stdout)
        stderr = _decoded(exc.stderr)
        stderr += f"\n[maestro-plus] flow exceeded the {timeout}s timeout and was killed."
    except OSError as exc:
        raise MaestroPlusError(f"Could not launch the Maestro CLI: {exc}") from exc

    duration = time.monotonic() - started
    success = exit_code == 0 and not timed_out

    junit_path = find_latest_junit(started_wall)
    failed_steps: list[str] = []
    if not success:
        if junit_path is not None:
            failed_steps = parse_junit(junit_path)
        if not failed_steps:
            failed_steps = parse_failed_steps(stdout)

    return FlowResult(
        flow=flow_path.name,
        device=device,
        success=success,
        exit_code=exit_code,
        duration_s=duration,
        junit_path=junit_path,
        failed_steps=failed_steps,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        timed_out=timed_out,
    )


def run_many(
    flows: Sequence[str | Path],
    *,
    device: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int = DEFAULT_FLOW_TIMEOUT,
    artifacts_dir: Path | None = None,
    on_result: callable | None = None,
) -> list[FlowResult]:
    """Run several flows in sequence on one device.

    Sequential on purpose. Concurrency belongs to the layer that owns device
    leases, because that layer is the only one that knows how many devices exist
    and which are already busy.
    """
    results: list[FlowResult] = []
    for flow in flows:
        try:
            result = run_flow(
                flow, device=device, env=env, timeout=timeout, artifacts_dir=artifacts_dir
            )
        except MaestroPlusError as exc:
            result = FlowResult(
                flow=Path(flow).name,
                device=device,
                success=False,
                exit_code=-1,
                duration_s=0.0,
                stderr_tail=str(exc),
            )
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results


def flatten(results: Iterable[FlowResult]) -> dict[str, object]:
    """Aggregate a set of results into a summary an agent can read at a glance."""
    items = list(results)
    if not items:
        return {"total": 0, "passed": 0, "failed": 0, "success": True, "results": []}

    failed = [r for r in items if not r.success]
    return {
        "total": len(items),
        "passed": len(items) - len(failed),
        "failed": len(failed),
        "success": not failed,
        "slowest_s": round(max(r.duration_s for r in items), 2),
        "failed_flows": [r.flow for r in failed],
        "results": [r.to_dict() for r in items],
    }


__all__ = [
    "FlowResult",
    "build_command",
    "flatten",
    "is_available",
    "parse_failed_steps",
    "parse_junit",
    "run_flow",
    "run_many",
    "version",
]
