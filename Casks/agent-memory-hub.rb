cask "agent-memory-hub" do
  version :latest
  sha256 :no_check

  url "https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh",
      verified: "github.com/liuyang0508/agent-memory-hub/"
  name "Agent Memory Hub"
  desc "Local-first shared memory layer for LLM agents"
  homepage "https://github.com/liuyang0508/agent-memory-hub"

  depends_on formula: "git"
  depends_on formula: "python@3.12"

  installer script: {
    executable: "/bin/sh",
    args: ["#{staged_path}/install.sh"],
  }

  uninstall script: {
    executable: "/bin/sh",
    args: ["#{staged_path}/install.sh", "--uninstall"],
  }

  caveats <<~EOS
    This cask is an installer channel. It delegates to the same install.sh
    entrypoint used by the GitHub Release installer and keeps user data under
    ~/.agent-memory-hub.

    If ~/.local/bin is not on PATH, add it before running:
      memory doctor
  EOS
end
