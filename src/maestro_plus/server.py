"""The MCP server.

Tool registration is centralised here instead of spread across decorators. That
keeps the complete surface this server *adds* readable in one place — and, by
omission, the surface it deliberately does not duplicate. The second property
matters more than the first: a project that reimplements its upstream's tools
dies the moment upstream ships them.
"""

from __future__ import annotations

import logging

from mcp.server import MCPServer

from .tools import assertions, devices, diagnostics, parallel, record

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
This server adds multi-device orchestration, assertions, and failure diagnosis on
top of the Maestro MCP server. It does not duplicate Maestro's own tools: for
reading the screen, tapping, or running a single flow with no expectations, use
those.

Start with health_check. It reports whether adb, the Maestro CLI, and a usable
device are present, and which of the tools below actually work right now. Several
tools cannot run without it, so calling one first spends a round trip on a
predictable error.

Then call list_device_pool to see how many devices are really free.

Choosing a tool:
  one flow, no expectations                  -> the official Maestro run tool
  one flow plus expectations about the result -> run_and_assert
  several flows across several devices        -> run_parallel
  something failed and you need to know whose fault it is -> debug_failure
  pixel comparison against a baseline          -> assert_visual
  turn an exploration into a reusable flow     -> explore_and_record

Every tool here leases a device for the duration of a run and releases it on exit,
including on failure. Do not point the official Maestro tool at a device while a
run here holds it.
"""

mcp = MCPServer("maestro-plus", instructions=INSTRUCTIONS)

#: The complete surface this server adds. Everything Maestro already exposes is
#: absent on purpose — wrap it, never reimplement it.
TOOLS = (
    devices.health_check,
    devices.list_device_pool,
    parallel.run_parallel,
    assertions.run_and_assert,
    assertions.assert_visual,
    diagnostics.debug_failure,
    record.explore_and_record,
)

for _tool in TOOLS:
    mcp.add_tool(_tool)


def main() -> None:
    """Run the server over stdio, which is the transport every MCP host expects.

    Synchronous and blocking by design. Plain ``def`` tools are dispatched to a
    worker thread by the SDK, so a flow that takes four minutes does not stall the
    server for anything else.
    """
    mcp.run()


if __name__ == "__main__":
    main()
