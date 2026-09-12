"""Backends that talk to the outside world.

Two of them, deliberately:

``maestro_cli``
    Wraps the Maestro command line. This is the primary path, and it is the same
    thing the official MCP server wraps internally — going straight to the CLI
    avoids running a second process and a second protocol hop.

``adb``
    Talks to devices directly. Used for discovery, evidence collection, and as
    the degradation path when neither the Maestro CLI nor the official MCP
    server is in a usable state.
"""

__all__: list[str] = []
