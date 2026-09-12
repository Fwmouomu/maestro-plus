#!/usr/bin/env node
/**
 * Launcher for the maestro-plus MCP server.
 *
 * This file carries no business logic and must not grow any. It finds a Python
 * interpreter, starts the real server, and forwards signals and the exit code.
 * Everything else lives in the Python package, because a second place for logic
 * is a second place for bugs to hide.
 *
 * The load-bearing detail is `stdio: "inherit"`. MCP speaks JSON-RPC over stdin
 * and stdout, so handing the child the parent's file descriptors directly means
 * there is no framing, no buffering, and no forwarding code that can corrupt a
 * message mid-flight. Piping instead of inheriting would require writing a proxy,
 * and a hand-written stdio proxy is where MCP servers break in ways that look
 * like the model misbehaving.
 *
 * Written as plain ESM rather than TypeScript on purpose. A zero-logic launcher
 * gains nothing from a build step, and a build step is one more thing that can
 * fail between `npx` and a working server.
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PACKAGE_NAME = "maestro-plus";
const PYTHON_MODULE = "maestro_plus";
const MIN_PYTHON = [3, 10];

const here = dirname(fileURLToPath(import.meta.url));
const isSourceCheckout =
  existsSync(join(here, "pyproject.toml")) && existsSync(join(here, "src", PYTHON_MODULE));


function log(message) {
  process.stderr.write(`${PACKAGE_NAME}: ${message}\n`);
}


function fail(message, hint) {
  log(message);
  if (hint) log(hint);
  process.exit(1);
}


function onPath(binary) {
  const probe = process.platform === "win32" ? "where" : "which";
  const result = spawnSync(probe, [binary], { encoding: "utf8" });
  return result.status === 0;
}


function pythonVersion(binary) {
  const result = spawnSync(
    binary,
    ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
    { encoding: "utf8" }
  );
  if (result.status !== 0) return null;

  const parts = result.stdout.trim().split(".").map(Number);
  return parts.length === 2 && parts.every(Number.isFinite) ? parts : null;
}


function isSupported(version) {
  return (
    version[0] > MIN_PYTHON[0] ||
    (version[0] === MIN_PYTHON[0] && version[1] >= MIN_PYTHON[1])
  );
}


function findPython() {
  const override = process.env.MAESTRO_PLUS_PYTHON;
  if (override) {
    if (!pythonVersion(override)) {
      fail(
        `MAESTRO_PLUS_PYTHON is set to "${override}", which is not a working Python interpreter.`,
        "Unset it to let the launcher pick an interpreter, or point it at one that runs."
      );
    }
    return override;
  }

  for (const candidate of ["python3", "python"]) {
    if (!onPath(candidate)) continue;
    const version = pythonVersion(candidate);
    if (version && isSupported(version)) return candidate;
  }
  return null;
}


function launch(command, args, extraEnv = {}) {
  const child = spawn(command, args, {
    stdio: ["inherit", "inherit", "inherit"],
    env: { ...process.env, ...extraEnv },
  });

  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => {
      if (!child.killed) child.kill(signal);
    });
  }

  child.on("error", (error) => {
    fail(`could not start ${command}: ${error.message}`);
  });

  child.on("exit", (code, signal) => {
    process.exit(signal ? 1 : code ?? 1);
  });
}


function main() {
  if (isSourceCheckout) {
    const python = findPython();
    if (!python) {
      fail(
        "running from a source checkout, but no Python 3.10+ interpreter was found.",
        "Install Python 3.10 or newer, or set MAESTRO_PLUS_PYTHON to the interpreter to use."
      );
    }
    log(`starting from the source checkout at ${here}`);
    launch(python, ["-m", PYTHON_MODULE], { PYTHONPATH: join(here, "src") });
    return;
  }

  if (onPath("uv")) {
    launch("uv", ["tool", "run", "--from", PACKAGE_NAME, PACKAGE_NAME]);
    return;
  }

  const python = findPython();
  if (python) {
    launch(python, ["-m", PYTHON_MODULE]);
    return;
  }

  fail(
    "no way to start the server was found on this machine.",
    "Install uv, which manages the Python dependency for you:\n" +
      "  curl -LsSf https://astral.sh/uv/install.sh | sh\n" +
      "or install Python 3.10+ and run `pip install maestro-plus`."
  );
}


main();
