#!/usr/bin/env node
"use strict";

const childProcess = require("child_process");
const fs = require("fs");
const path = require("path");

const packageRoot = path.resolve(__dirname, "../../..");
const packageJson = require(path.join(packageRoot, "package.json"));

if (process.env.AGENT_MEMORY_HUB_NPM_SKIP_INSTALL === "1") {
  console.log("agent-memory-hub: npm postinstall skipped by AGENT_MEMORY_HUB_NPM_SKIP_INSTALL=1");
  process.exit(0);
}

const installerPath =
  process.platform === "win32"
    ? path.join(packageRoot, "install.ps1")
    : path.join(packageRoot, "install.sh");

if (!fs.existsSync(installerPath)) {
  console.error(`agent-memory-hub: installer missing from npm package: ${installerPath}`);
  process.exit(1);
}

const env = {
  ...process.env,
  AMH_INSTALL_SOURCE: process.env.AMH_INSTALL_SOURCE || "npm",
};

if (!env.AMH_REF && !env.AMH_BRANCH && !env.AMH_RELEASE_REF && packageJson.version) {
  env.AMH_RELEASE_REF = `v${packageJson.version}`;
}

const extraArgs = (process.env.AGENT_MEMORY_HUB_NPM_INSTALL_ARGS || "")
  .split(/\s+/)
  .filter(Boolean);

const command =
  process.platform === "win32"
    ? {
        executable: "powershell",
        args: ["-ExecutionPolicy", "ByPass", "-File", installerPath, ...extraArgs],
      }
    : {
        executable: "sh",
        args: [installerPath, ...extraArgs],
      };

const result = childProcess.spawnSync(command.executable, command.args, {
  stdio: "inherit",
  env,
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status === null ? 1 : result.status);
