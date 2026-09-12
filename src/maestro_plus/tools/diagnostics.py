"""Failure diagnosis.

This tool is the reason the project exists. Everything else here is plumbing;
this answers the question a QA engineer actually asks first, which is never "did
it pass" but "is this the app's fault or mine".

The attribution is a heuristic and it says so out loud. Every verdict carries a
confidence, and a low-confidence verdict is reported as low rather than rounded
up. A diagnosis that claims certainty it does not have is worse than no diagnosis
at all: it sends a bug report to the wrong team and teaches everyone to distrust
the tool.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .. import evidence, report
from ..backends import adb, maestro_cli
from ..errors import MaestroPlusError, ToolError
from ..pool import POOL

logger = logging.getLogger(__name__)

_QUOTED_RE = re.compile(r"""["'\u201c\u2018]([^"'\u201d\u2019]{2,})["'\u201d\u2019]""")
_CRASH_MARKERS = ("FATAL EXCEPTION", "beginning of crash")
_ANR_RE = re.compile(r"ANR in (\S+)")
_SYSTEM_DIALOGS = ("permissioncontroller", "packageinstaller")


def _maestro_artifacts_since(since: float) -> list[Path]:
    """Files Maestro wrote during the run that just finished.

    Maestro captures its own screenshot on failure, and that frame is worth more
    than anything taken afterwards: it shows the screen at the moment of failure
    rather than the state the app settled into once the flow gave up.
    """
    root = maestro_cli.MAESTRO_OUTPUT_ROOT
    if not root.is_dir():
        return []

    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime >= since:
                found.append(path)
        except OSError:
            continue
    return found


def _attribute(
    result: maestro_cli.FlowResult,
    log_text: str,
    focus: str | None,
    hierarchy,
) -> tuple[str, str, list[str], list[str]]:
    """Return ``(attribution, confidence, findings, recommendations)``.

    Ordered from unambiguous to ambiguous. A crash is answerable; a locator that
    does not resolve is not, because one failed run genuinely cannot tell a stale
    test apart from a screen that never arrived.
    """
    findings: list[str] = []
    recommendations: list[str] = []

    crash_marker = next((marker for marker in _CRASH_MARKERS if marker in log_text), None)
    if crash_marker:
        findings.append(
            f"logcat contains {crash_marker!r}, so the application process died during the run."
        )
        recommendations.append(
            "Read the stack trace in the log excerpt below and file it against the app."
        )
        return report.ATTRIBUTION_APPLICATION, "high", findings, recommendations

    anr = _ANR_RE.search(log_text)
    if anr:
        findings.append(
            f"logcat records an ANR in {anr.group(1)}: the app stopped responding for over "
            "five seconds."
        )
        recommendations.append(
            "Look for main-thread blocking work. An ANR is an application defect, not a "
            "flaky test."
        )
        return report.ATTRIBUTION_APPLICATION, "high", findings, recommendations

    if focus:
        lowered = focus.lower()
        if any(dialog in lowered for dialog in _SYSTEM_DIALOGS):
            findings.append(f"A system dialog holds focus ({focus}), and the flow never dismissed it.")
            recommendations.append(
                "This is a test defect. Either add a step that dismisses the dialog or grant "
                "the permission ahead of the run with `adb shell pm grant`."
            )
            return report.ATTRIBUTION_TEST, "high", findings, recommendations
        if lowered.startswith("com.android.systemui"):
            findings.append(f"A system UI surface holds focus ({focus}).")
            recommendations.append(
                "Something interrupted the app: a notification shade, a system prompt, or the "
                "lock screen. That is an environment problem, not an app bug."
            )
            return report.ATTRIBUTION_ENVIRONMENT, "medium", findings, recommendations

    if hierarchy is None:
        findings.append(
            "The view hierarchy could not be read, so no element-level reasoning was possible."
        )
        recommendations.append(
            "Run `adb shell uiautomator dump` by hand. If it fails too, the app is showing a "
            "surface uiautomator cannot inspect — a WebView, a Flutter canvas, or a game view."
        )
        return report.ATTRIBUTION_ENVIRONMENT, "medium", findings, recommendations

    step = result.failed_steps[0] if result.failed_steps else None
    quoted = _QUOTED_RE.search(step) if step else None

    if quoted:
        target = quoted.group(1)
        matches = evidence.find_elements(hierarchy, text=target, visible_only=False)
        matches += evidence.find_elements(hierarchy, resource_id=target, visible_only=False)

        if not matches:
            findings.append(
                f"The failing step refers to {target!r}, which appears nowhere in the view "
                "hierarchy."
            )
            findings.append(
                "Two explanations fit equally well: the app never reached the screen this step "
                "expects, or the flow points at a label this build does not contain. One failed "
                "run cannot separate them."
            )
            recommendations.append(
                "Re-run with a capture immediately before this step. If the expected screen is "
                "absent, it is an application defect; if it is present, the locator is stale."
            )
            return report.ATTRIBUTION_UNKNOWN, "low", findings, recommendations

        unrendered = [match for match in matches if not match.is_visible]
        if len(unrendered) == len(matches):
            findings.append(
                f"{target!r} exists in the hierarchy but has zero-area bounds, so it is not "
                "actually rendered."
            )
            recommendations.append(
                "A present-but-invisible element usually means a layout or visibility "
                "regression rather than a test problem."
            )
            return report.ATTRIBUTION_APPLICATION, "medium", findings, recommendations

        disabled = [match for match in matches if not match.enabled]
        if disabled:
            findings.append(
                f"{target!r} is present but disabled, so interacting with it does nothing."
            )
            recommendations.append(
                "Check the app's validation or gating logic. A disabled control where the flow "
                "expects an active one is normally an application defect."
            )
            return report.ATTRIBUTION_APPLICATION, "medium", findings, recommendations

        findings.append(
            f"{target!r} is present, enabled, and visible at the moment of inspection."
        )
        findings.append(
            "A reachable element points away from a missing or renamed control and towards "
            "timing, a missed precondition, or state left behind by an earlier step."
        )
        recommendations.append(
            "Add an explicit wait or a precondition assertion before this step. If it still "
            "fails, the app may be slow rather than broken."
        )
        return report.ATTRIBUTION_TEST, "medium", findings, recommendations

    findings.append(
        "No crash, no system interruption, and no locator problem could be identified from the "
        "evidence collected."
    )
    recommendations.append(
        "Open the bundled HTML report and read the screen at failure. The heuristic needed "
        "evidence it did not have."
    )
    return report.ATTRIBUTION_UNKNOWN, "low", findings, recommendations


def debug_failure(
    flow: Annotated[str, Field(description="Maestro flow file to run and diagnose.")],
    device: Annotated[
        str | None, Field(description="Device serial. Omit to take any single free device.")
    ] = None,
    env: Annotated[
        dict[str, str] | None, Field(description="Maestro -e variables for the flow.")
    ] = None,
    timeout: Annotated[int, Field(ge=10, le=7200, description="Flow timeout in seconds.")] = 600,
    package: Annotated[
        str | None,
        Field(
            description="App package to narrow logcat to, for example com.example.app. Omit to "
            "read the whole buffer."
        ),
    ] = None,
) -> dict:
    """Run a flow and, when it fails, gather the evidence needed to attribute the fault.

    Collects the screen before the run, Maestro's own capture at the moment of
    failure, the screen after, the focused window, the relevant slice of logcat,
    and the elements present at failure — then applies an attribution heuristic
    and writes a JSON plus HTML bundle.

    Returns ``diagnosed: false`` when the flow passes, because there is nothing to
    diagnose and inventing an analysis would be noise.
    """
    flow_path = Path(flow).expanduser()
    if not flow_path.exists():
        raise ToolError(
            f"Flow file not found: {flow_path}. Paths resolve against the server working "
            "directory, not the client's."
        )

    with POOL.lease(count=1, preferred=[device] if device else None) as lease:
        serial = lease.devices[0]
        run_dir = report.new_run_dir(f"debug-{flow_path.stem}")
        started_wall = time.time()

        before: Path | None = None
        try:
            before = adb.screenshot(serial, run_dir / "before.png")
        except MaestroPlusError as exc:
            logger.warning("pre-run screenshot failed on %s: %s", serial, exc)

        result = maestro_cli.run_flow(flow_path, device=serial, env=env, timeout=timeout)

        if result.success:
            return {
                "diagnosed": False,
                "flow": result.to_dict(),
                "verdict": f"Flow '{result.flow}' passed on {serial}; there is nothing to diagnose.",
                "artifacts_dir": str(run_dir),
            }

        after: Path | None = None
        try:
            after = adb.screenshot(serial, run_dir / "after.png")
        except MaestroPlusError as exc:
            logger.warning("post-run screenshot failed on %s: %s", serial, exc)

        focus = adb.current_focus(serial)

        hierarchy = None
        try:
            hierarchy = adb.dump_hierarchy(serial)
        except MaestroPlusError as exc:
            logger.warning("view hierarchy unavailable on %s: %s", serial, exc)

        relevant_log = evidence.relevant_log_lines(
            adb.recent_logs(serial, lines=600, package=package)
        )
        maestro_shots = [
            path
            for path in _maestro_artifacts_since(started_wall)
            if path.suffix.lower() == ".png"
        ]

        candidates = [path for path in [before, *maestro_shots, after] if path is not None]
        frames = evidence.dedupe_screenshots(candidates)
        kept = evidence.unique_paths(frames)

        attribution, confidence, findings, recommendations = _attribute(
            result, relevant_log, focus, hierarchy
        )

        step_phrase = f" Failing step: {result.failed_steps[0]}." if result.failed_steps else ""
        diagnosis = report.Diagnosis(
            flow=result.flow,
            device=serial,
            verdict=(
                f"Flow '{result.flow}' failed on {serial} after {result.duration_s:.1f}s."
                f"{step_phrase} {findings[0] if findings else ''}"
            ).strip(),
            attribution=attribution,
            confidence=confidence,
            failed_steps=result.failed_steps,
            findings=findings,
            recommendations=recommendations,
            evidence={
                "screen_before_run": str(before) if before else "not captured",
                "screen_at_failure": str(maestro_shots[0]) if maestro_shots else "Maestro captured none",
                "screen_after_failure": str(after) if after else "not captured",
                "screens_kept": len(kept),
                "screens_dropped_as_duplicates": len(candidates) - len(kept),
                "focused_window": focus or "unknown",
                "relevant_log": relevant_log or "(no fault markers found in the log buffer)",
                "on_screen_at_failure": (
                    evidence.describe_screen(hierarchy, limit=40)
                    if hierarchy is not None
                    else "hierarchy unavailable on this device"
                ),
                "flow_stdout_tail": result.stdout_tail,
                "flow_stderr_tail": result.stderr_tail,
            },
            artifacts_dir=str(run_dir),
            duration_s=result.duration_s,
            exit_code=result.exit_code,
        )

        written = report.write_bundle(diagnosis, run_dir)

    return {
        "diagnosed": True,
        "flow": result.to_dict(),
        "attribution": attribution,
        "confidence": confidence,
        "verdict": diagnosis.verdict,
        "findings": findings,
        "recommendations": recommendations,
        "evidence": diagnosis.evidence,
        "artifacts_dir": str(run_dir),
        "report_html": str(written["html"]),
        "report_json": str(written["json"]),
    }


__all__ = ["debug_failure"]
