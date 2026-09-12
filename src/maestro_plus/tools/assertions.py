"""Assertions evaluated in the same call as the flow that produced the screen.

Without this, an agent runs a flow, asks for a screenshot, asks what is on screen,
reasons about it, and the judgement about whether the app behaved correctly lives
only inside the model's context — unverifiable and unrepeatable. These tools
collapse that into one call that returns a typed answer.

Failure policy is deliberate: assertions fail closed. When the screen cannot be
read, every assertion is reported as failed with the reason, never as passed. An
assertion that silently succeeds because evidence was missing is worse than no
assertion at all.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .. import evidence, report
from ..backends import adb, maestro_cli
from ..errors import MaestroPlusError, ToolError
from ..pool import POOL

logger = logging.getLogger(__name__)


class ElementAssertion(BaseModel):
    """One expectation about the screen after a flow has finished."""

    kind: Literal["visible", "not_visible", "contains_text"] = Field(
        description="visible: the element must be present. not_visible: it must be absent. "
        "contains_text: some matched element must contain the given text."
    )
    text: str | None = Field(
        default=None, description="Match against the element's visible text."
    )
    resource_id: str | None = Field(default=None, description="Match against resource-id.")
    content_desc: str | None = Field(default=None, description="Match against content-desc.")
    expected: str | None = Field(
        default=None, description="Required text. Only read by the contains_text kind."
    )
    contains: bool = Field(
        default=True, description="Substring match rather than exact match."
    )


def _subject(assertion: ElementAssertion) -> str:
    locator = assertion.text or assertion.resource_id or assertion.content_desc
    if locator:
        return repr(locator)
    if assertion.kind == "contains_text":
        return f"any element containing {assertion.expected!r}"
    return "any element"


def _evaluate(root, assertion: ElementAssertion) -> tuple[bool, str]:
    """Return ``(passed, explanation)``.

    The explanation is written for a model to act on: it names what was searched
    for and what was found instead, so the next attempt can differ from this one.
    """
    matches = evidence.find_elements(
        root,
        text=assertion.text,
        resource_id=assertion.resource_id,
        content_desc=assertion.content_desc,
        contains=assertion.contains,
    )
    subject = _subject(assertion)

    if assertion.kind == "visible":
        if matches:
            return True, f"{subject} is on screen ({len(matches)} match(es))."
        return False, f"{subject} was not found on screen."

    if assertion.kind == "not_visible":
        if not matches:
            return True, f"{subject} is absent, as expected."
        return False, (
            f"{subject} is still on screen ({len(matches)} match(es)); "
            f"the first sits at bounds {matches[0].bounds}."
        )

    expected = assertion.expected or ""
    if not expected:
        return False, "contains_text needs `expected` to be set; nothing was checked."

    if any(expected.lower() in element.text.lower() for element in matches):
        return True, f"{subject} contains {expected!r}."

    if not matches:
        return False, f"Nothing matching {subject} was on screen, so {expected!r} could not appear."
    actual = ", ".join(repr(element.text) for element in matches[:3])
    return False, (
        f"{subject} is present, but none of its {len(matches)} match(es) contain "
        f"{expected!r}. Present instead: {actual}."
    )


def _verdict(flow_name: str, result: maestro_cli.FlowResult, outcomes: list[dict]) -> str:
    """One sentence an agent can quote without reading anything else."""
    if not result.success:
        detail = f" Failing step(s): {', '.join(result.failed_steps)}." if result.failed_steps else ""
        return f"Flow '{flow_name}' failed, so assertions were not evaluated.{detail}"
    if not outcomes:
        return f"Flow '{flow_name}' passed. No assertions were requested."

    failed = [outcome for outcome in outcomes if not outcome["passed"]]
    if not failed:
        return f"Flow '{flow_name}' passed and all {len(outcomes)} assertion(s) held."
    return (
        f"Flow '{flow_name}' passed, but {len(failed)} of {len(outcomes)} assertion(s) failed: "
        + " ".join(str(outcome["detail"]) for outcome in failed[:3])
    )


def run_and_assert(
    flow: Annotated[str, Field(description="Maestro flow file to run.")],
    assertions: Annotated[
        list[ElementAssertion],
        Field(description="Expectations checked against the screen once the flow finishes."),
    ],
    device: Annotated[
        str | None,
        Field(description="Device serial. Omit to take any single free device from the pool."),
    ] = None,
    env: Annotated[
        dict[str, str] | None, Field(description="Maestro -e variables for the flow.")
    ] = None,
    timeout: Annotated[int, Field(ge=10, le=7200, description="Flow timeout in seconds.")] = 600,
    screenshot: Annotated[
        bool, Field(description="Capture the resulting screen as evidence.")
    ] = True,
) -> dict:
    """Run a Maestro flow, then check assertions against the screen it left behind.

    The flow's own result is always reported, even when assertions cannot run.
    Assertions are skipped entirely when the flow failed, because checking
    expectations after a known-broken run produces a second, noisier failure that
    buries the first one.
    """
    flow_path = Path(flow).expanduser()
    if not flow_path.exists():
        raise ToolError(
            f"Flow file not found: {flow_path}. Paths resolve against the server working "
            "directory, not the client's."
        )

    outcomes: list[dict] = []
    shot: Path | None = None

    with POOL.lease(count=1, preferred=[device] if device else None) as lease:
        serial = lease.devices[0]
        run_dir = report.new_run_dir(f"assert-{flow_path.stem}")
        result = maestro_cli.run_flow(flow_path, device=serial, env=env, timeout=timeout)

        if screenshot:
            try:
                shot = adb.screenshot(serial, run_dir / "after.png")
            except MaestroPlusError as exc:
                logger.warning("screenshot failed on %s: %s", serial, exc)

        if result.success and assertions:
            try:
                root = adb.dump_hierarchy(serial)
            except MaestroPlusError as exc:
                outcomes = [
                    {
                        "assertion": assertion.model_dump(),
                        "passed": False,
                        "detail": f"Screen could not be read, so this was not verified: {exc}",
                    }
                    for assertion in assertions
                ]
            else:
                for assertion in assertions:
                    passed, detail = _evaluate(root, assertion)
                    outcomes.append(
                        {"assertion": assertion.model_dump(), "passed": passed, "detail": detail}
                    )

    return {
        "flow": result.to_dict(),
        "assertions": outcomes,
        "passed": result.success and all(outcome["passed"] for outcome in outcomes),
        "verdict": _verdict(result.flow, result, outcomes),
        "screenshot": str(shot) if shot else None,
        "artifacts_dir": str(run_dir),
    }


def assert_visual(
    baseline: Annotated[str, Field(description="Path to the baseline PNG to compare against.")],
    device: Annotated[
        str | None, Field(description="Device serial. Omit to take any single free device.")
    ] = None,
    region: Annotated[
        list[int] | None,
        Field(
            description="[left, top, right, bottom] in pixels to compare. Omit to compare the "
            "whole screen."
        ),
    ] = None,
    tolerance: Annotated[
        float,
        Field(ge=0.0, le=1.0, description="Fraction of differing pixels still considered a match."),
    ] = 0.02,
    update_baseline: Annotated[
        bool, Field(description="Overwrite the baseline with the current screen and pass.")
    ] = False,
) -> dict:
    """Compare the current screen, or a region of it, against a baseline image.

    Region comparison is the reason this is usable. A whole-screen baseline on a
    device with a clock in the status bar differs on every single run, which is
    how visual testing earns its reputation for flakiness. Cropping to the part
    that matters is what makes the result mean something.

    ``update_baseline`` exists so refreshing a baseline is an explicit, visible act
    rather than an edit someone makes by hand and forgets to review.
    """
    baseline_path = Path(baseline).expanduser()
    if not baseline_path.exists() and not update_baseline:
        raise ToolError(
            f"Baseline image not found: {baseline_path}. Pass update_baseline=true to "
            "create it from the current screen."
        )

    with POOL.lease(count=1, preferred=[device] if device else None) as lease:
        serial = lease.devices[0]
        run_dir = report.new_run_dir("visual")
        current = adb.screenshot(serial, run_dir / "current.png")

        if update_baseline:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_bytes(current.read_bytes())
            return {
                "passed": True,
                "updated": True,
                "baseline": str(baseline_path),
                "current": str(current),
                "verdict": f"Baseline replaced with the current screen from {serial}.",
            }

        comparison = _compare_images(baseline_path, current, region, tolerance)

    return {
        **comparison,
        "updated": False,
        "baseline": str(baseline_path),
        "current": str(current),
        "region": region,
        "tolerance": tolerance,
        "verdict": comparison.get("reason")
        or (
            f"{comparison.get('difference_ratio', 0):.2%} of pixels differ "
            f"(tolerance {tolerance:.0%})"
        ),
    }


def _compare_images(
    baseline_path: Path,
    current_path: Path,
    region: list[int] | None,
    tolerance: float,
) -> dict:
    """Pixel comparison, returning a verdict rather than raising on mismatch."""
    try:
        from PIL import Image, ImageChops
    except ImportError as exc:
        raise ToolError(
            "Visual comparison needs Pillow. Install it with `pip install Pillow`, or run "
            "health_check to see which tools are affected."
        ) from exc

    with Image.open(baseline_path) as baseline_image, Image.open(current_path) as current_image:
        baseline = baseline_image.convert("RGB")
        current = current_image.convert("RGB")

        if baseline.size != current.size:
            return {
                "passed": False,
                "comparable": False,
                "reason": (
                    f"Screen size changed: baseline is {baseline.size[0]}x{baseline.size[1]}, "
                    f"current is {current.size[0]}x{current.size[1]}. Regenerate the baseline "
                    "or the comparison is meaningless."
                ),
            }

        if region:
            if len(region) != 4:
                raise ToolError("region must be exactly [left, top, right, bottom].")
            box = tuple(region)
            baseline = baseline.crop(box)
            current = current.crop(box)

        difference = ImageChops.difference(baseline, current).convert("L")
        # Anything under 16 levels is compression and anti-aliasing noise, not a change.
        mask = difference.point(lambda value: 255 if value > 16 else 0)
        differing = mask.histogram()[255]

    total = baseline.size[0] * baseline.size[1]
    ratio = differing / total if total else 0.0

    return {
        "passed": ratio <= tolerance,
        "comparable": True,
        "differing_pixels": differing,
        "total_pixels": total,
        "difference_ratio": round(ratio, 4),
    }


__all__ = ["ElementAssertion", "assert_visual", "run_and_assert"]
