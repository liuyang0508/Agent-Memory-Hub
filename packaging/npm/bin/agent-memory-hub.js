#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const packageRoot = path.resolve(__dirname, "../../..");
const packageJson = require(path.join(packageRoot, "package.json"));

function printHelp() {
  console.log(`agent-memory-hub ${packageJson.version}

Usage:
  agent-memory-hub install [-- --minimal]
  agent-memory-hub verify
  agent-memory-hub uninstall
  agent-memory-hub doctor

The npm package is an installer channel. It delegates installation to the same
install.sh / install.ps1 entrypoints used by the GitHub Release installer.

Environment:
  AGENT_MEMORY_HUB_NPM_SKIP_INSTALL=1  skip npm postinstall
  AMH_REF / AMH_BRANCH / AMH_RELEASE_REF  choose the Git ref to install
`);
}

function installerCommand(extraArgs) {
  if (process.platform === "win32") {
    return {
      command: "powershell",
      args: ["-ExecutionPolicy", "ByPass", "-File", path.join(packageRoot, "install.ps1"), ...extraArgs],
    };
  }
  return {
    command: "sh",
    args: [path.join(packageRoot, "install.sh"), ...extraArgs],
  };
}

function run(command, args, options = {}) {
  const result = childProcess.spawnSync(command, args, {
    stdio: "inherit",
    env: {
      ...process.env,
      AMH_INSTALL_SOURCE: process.env.AMH_INSTALL_SOURCE || "npm",
    },
    ...options,
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  process.exit(result.status === null ? 1 : result.status);
}

const [command, ...rest] = process.argv.slice(2);

if (!command || command === "help" || command === "--help" || command === "-h") {
  printHelp();
  process.exit(0);
}

if (command === "--version" || command === "version") {
  console.log(packageJson.version);
  process.exit(0);
}

if (!fs.existsSync(path.join(packageRoot, "install.sh"))) {
  console.error("install.sh is missing from the npm package.");
  process.exit(1);
}

if (command === "install") {
  const passthrough = rest[0] === "--" ? rest.slice(1) : rest;
  const installer = installerCommand(passthrough);
  run(installer.command, installer.args);
}

if (command === "verify" || command === "verify-only") {
  const installer = installerCommand(["--verify-only"]);
  run(installer.command, installer.args);
}

if (command === "uninstall") {
  const installer = installerCommand(["--uninstall"]);
  run(installer.command, installer.args);
}

if (command === "doctor") {
  run("memory", ["doctor", ...rest]);
}

console.error(`Unknown command: ${command}`);
printHelp();
process.exit(2);
