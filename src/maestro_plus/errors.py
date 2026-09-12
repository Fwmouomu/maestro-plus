"""Error types used across the package.

The MCP Python SDK moved its error classes in v2. A raised ``ToolError`` has its
message delivered to the model, which is what makes a failure recoverable: the
model reads "device emulator-5554 is offline" and retries somewhere else. Any
other exception type is swallowed into a generic ``Error executing tool <name>``
string, which tells the model nothing.

So this module exists to guarantee one thing: ``ToolError`` always resolves to
something usable, no matter which SDK version is installed.
"""

from __future__ import annotations

try:  # SDK v2 layout
    from mcp.server.mcpserver import ToolError
except ImportError:  # pragma: no cover - depends on installed SDK
    try:  # SDK layouts that re-export from the package root
        from mcp.server import ToolError  # type: ignore[attr-defined,no-redef]
    except ImportError:

        class ToolError(Exception):  # type: ignore[no-redef]
            """Fallback so the package imports even without a usable SDK.

            Loses the "message reaches the model" behaviour, but keeps every
            other code path working. ``health_check`` reports when this happens.
            """


class MaestroPlusError(RuntimeError):
    """Base class for every error this package raises on purpose."""


class ToolchainError(MaestroPlusError):
    """A required external tool is missing or unusable."""


class DeviceError(MaestroPlusError):
    """A device was requested that is not present, not usable, or already leased."""


class FlowError(MaestroPlusError):
    """A Maestro flow failed to run, or its output could not be interpreted."""


__all__ = [
    "DeviceError",
    "FlowError",
    "MaestroPlusError",
    "ToolError",
    "ToolchainError",
]
