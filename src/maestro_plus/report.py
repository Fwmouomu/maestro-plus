"""Diagnosis bundles and rendered reports.

The output of a failed mobile run should answer one question before any other:
**is this the application or the test?** Everything in this module exists to make
that answer obvious to whoever reads it next — a human, or the model that has to
decide whether to file a bug or fix a locator.
"""

from __future__ import annotations

import html
import itertools
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ARTIFACTS_ENV = "MAESTRO_PLUS_HOME"
_SESSION_COUNTER = itertools.count(1)

ATTRIBUTION_APPLICATION = "application"
ATTRIBUTION_TEST = "test"
ATTRIBUTION_ENVIRONMENT = "environment"
ATTRIBUTION_UNKNOWN = "unknown"


def home() -> Path:
    """Root for all artifacts, overridable so CI can keep them inside a workspace."""
    return Path(os.environ.get(ARTIFACTS_ENV) or (Path.home() / ".maestro-plus"))


def new_run_dir(prefix: str = "run") -> Path:
    """A collision-free directory for one run's artifacts.

    The session counter matters: two runs inside the same second in the same
    process are otherwise indistinguishable, and overwriting the first run's
    evidence with the second's is a silent way to lose the bug you were chasing.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = home() / "artifacts" / f"{prefix}-{stamp}-{os.getpid()}-{next(_SESSION_COUNTER)}"
    target.mkdir(parents=True, exist_ok=True)
    return target


@dataclass
class Diagnosis:
    """A structured answer to "why did this run fail?"."""

    flow: str
    device: str | None
    verdict: str
    attribution: str = ATTRIBUTION_UNKNOWN
    confidence: str = "low"
    failed_steps: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    evidence: dict[str, object] = field(default_factory=dict)
    artifacts_dir: str | None = None
    duration_s: float | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def write_bundle(diagnosis: Diagnosis, directory: Path) -> dict[str, Path]:
    """Write the diagnosis as machine-readable JSON plus a standalone HTML page.

    Both, rather than one: the JSON is what an agent or a CI job consumes, and the
    HTML is what a person opens when they want to see the screenshots.
    """
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "diagnosis.json"
    html_path = directory / "diagnosis.html"

    json_path.write_text(diagnosis.to_json(), encoding="utf-8")
    html_path.write_text(render_html(diagnosis), encoding="utf-8")
    logger.info("diagnosis written to %s", directory)

    return {"json": json_path, "html": html_path}


_ATTRIBUTION_LABEL = {
    ATTRIBUTION_APPLICATION: "Looks like an application defect",
    ATTRIBUTION_TEST: "Looks like a test defect",
    ATTRIBUTION_ENVIRONMENT: "Environment or toolchain problem",
    ATTRIBUTION_UNKNOWN: "Could not be determined",
}


def render_html(diagnosis: Diagnosis) -> str:
    """Render a self-contained report page.

    No external assets and no build step. The report has to open correctly from a
    CI artifact zip on a machine that has never seen this project.
    """

    def list_items(items: list[str]) -> str:
        if not items:
            return '<p class="muted">None.</p>'
        return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"

    evidence_rows: list[str] = []
    for key, value in diagnosis.evidence.items():
        if isinstance(value, str) and value.lower().endswith((".png", ".jpg", ".jpeg")):
            name = Path(value).name
            evidence_rows.append(
                f'<figure><img src="{html.escape(name)}" alt="{html.escape(name)}">'
                f"<figcaption>{html.escape(key)}: {html.escape(name)}</figcaption></figure>"
            )
        elif isinstance(value, str) and "\n" in value:
            evidence_rows.append(
                f"<h3>{html.escape(key)}</h3><pre>{html.escape(value)}</pre>"
            )
        elif isinstance(value, list | dict):
            rendered = json.dumps(value, indent=2, ensure_ascii=False)
            evidence_rows.append(
                f"<h3>{html.escape(key)}</h3><pre>{html.escape(rendered)}</pre>"
            )
        else:
            evidence_rows.append(
                f"<h3>{html.escape(key)}</h3><pre>{html.escape(str(value))}</pre>"
            )

    verification = html.escape(" ".join(diagnosis.failed_steps)) or diagnosis.flow
    attribution_label = _ATTRIBUTION_LABEL.get(diagnosis.attribution, diagnosis.attribution)
    duration = f"{diagnosis.duration_s:.1f}s" if diagnosis.duration_s else "n/a"
    reproduce = f"maestro test {diagnosis.flow}"
    if diagnosis.device:
        reproduce += f" --device {diagnosis.device}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>maestro-plus diagnosis: {html.escape(diagnosis.flow)}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0; padding: 32px; font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
         color: #2c2c2a; background: #ffffff; max-width: 900px; }}
  h1 {{ font-size: 20px; font-weight: 500; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; font-weight: 500; margin: 32px 0 8px; padding-bottom: 6px;
        border-bottom: 1px solid #e2e8f0; }}
  h3 {{ font-size: 13px; font-weight: 500; margin: 20px 0 6px; color: #444441; }}
  p, li {{ font-size: 14px; }}
  .muted {{ color: #888780; }}
  .verdict {{ font-size: 17px; font-weight: 500; margin: 16px 0 8px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 6px; font-size: 12px;
            font-weight: 500; background: #f1efe8; border: 1px solid #d3d1c7; }}
  .meta {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 20px;
           font-size: 13px; margin-top: 16px; }}
  .meta dt {{ color: #888780; }}
  .meta dd {{ margin: 0; }}
  pre {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;
         overflow-x: auto; font-size: 12px; line-height: 1.5; white-space: pre-wrap; }}
  figure {{ margin: 0 0 20px; }}
  img {{ max-width: 100%; border: 1px solid #e2e8f0; border-radius: 8px; }}
  figcaption {{ font-size: 12px; color: #888780; margin-top: 6px; }}
  ul {{ padding-left: 20px; }}
</style>
</head>
<body>
<h1>Run diagnosis</h1>
<p class="muted">Generated by maestro-plus</p>

<p class="verdict">{html.escape(diagnosis.verdict)}</p>
<span class="badge">{html.escape(attribution_label)}</span>
<span class="badge">confidence: {html.escape(diagnosis.confidence)}</span>

<dl class="meta">
  <dt>Flow</dt><dd>{html.escape(diagnosis.flow)}</dd>
  <dt>Device</dt><dd>{html.escape(diagnosis.device or "default")}</dd>
  <dt>Exit code</dt><dd>{html.escape(str(diagnosis.exit_code))}</dd>
  <dt>Duration</dt><dd>{html.escape(duration)}</dd>
  <dt>Artifacts</dt><dd>{html.escape(diagnosis.artifacts_dir or "n/a")}</dd>
</dl>

<h2>Findings</h2>
{list_items(diagnosis.findings)}

<h2>Recommendations</h2>
{list_items(diagnosis.recommendations)}

<h2>Evidence</h2>
{"".join(evidence_rows) or '<p class="muted">No evidence collected.</p>'}

<h2>Reproduce</h2>
<pre>{html.escape(reproduce)}</pre>
<p class="muted">Failed at: {verification}</p>
</body>
</html>
"""


__all__ = [
    "ARTIFACTS_ENV",
    "ATTRIBUTION_APPLICATION",
    "ATTRIBUTION_ENVIRONMENT",
    "ATTRIBUTION_TEST",
    "ATTRIBUTION_UNKNOWN",
    "Diagnosis",
    "home",
    "new_run_dir",
    "render_html",
    "write_bundle",
]
