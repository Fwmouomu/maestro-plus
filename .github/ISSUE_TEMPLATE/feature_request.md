---
name: Feature request
about: A capability that is missing
labels: enhancement
---

**Which gap does this fill?**

This project is an orchestration layer above [Maestro's official MCP server](https://github.com/mobile-dev-inc/Maestro), so every feature should map to something upstream does not do. Name the gap:

- Is there an upstream issue for it? (link it)
- Or is it a paid Cloud capability? (those will not land in the open-source CLI, which makes them fair game)
- Or a gap this repo's [docs/why.md](../docs/why.md) does not list yet?

**Why orchestration above the official tools is the right place**

If the capability can be built by composing existing official tools plus local logic, this repo is the right place. If it requires changing Maestro itself, open the issue upstream first and link it here — a fork that reimplements upstream is the specific way this project dies.

**Proposed behavior**

<!-- What the tool takes, what it returns, and what it should do when evidence is missing. Tools here fail closed: an assertion that silently passes because evidence was missing is worse than no assertion. -->
