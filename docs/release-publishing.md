# Release Publishing

This project has three public install channels. They share one installer path:
`install.sh` on macOS/Linux and `install.ps1` on Windows.

## GitHub Release Assets

The README curl commands require a GitHub Release with these assets:

- `install.sh`
- `install.ps1`
- `checksums.txt`

Publishing a tag like `v1.1.0` runs `.github/workflows/release-installers.yml`
and uploads the installer assets to the GitHub Release.

## npm

The npm package is an installer channel. It publishes the root `package.json`,
the two installer scripts, and the npm wrapper under `packaging/npm/`.

Manual publish:

```bash
npm pack --dry-run
npm publish --access public
```

GitHub Actions publish:

- Add `NPM_TOKEN` as a repository secret.
- Push a `v*` tag or run `.github/workflows/publish-npm.yml` manually.

Users can skip automatic install during package installation:

```bash
AGENT_MEMORY_HUB_NPM_SKIP_INSTALL=1 npm install -g agent-memory-hub
agent-memory-hub install
```

## Homebrew

Publish a standard Homebrew tap repository at:

```text
https://github.com/liuyang0508/homebrew-agent-memory-hub
```

It must contain:

```text
Casks/agent-memory-hub.rb
```

Then users can install with one command:

```bash
brew install --cask liuyang0508/agent-memory-hub/agent-memory-hub
```

Homebrew maps `liuyang0508/agent-memory-hub` to the GitHub repository
`liuyang0508/homebrew-agent-memory-hub`. The cask delegates to the main
repository's GitHub Release `install.sh` asset, so the cask requires the release
installer asset to exist.

## Gitee Mirror

The Gitee mirror is published at:

```text
https://gitee.com/liuyang0508/Agent-Memory-Hub
```

`.github/workflows/sync-gitee.yml` syncs GitHub branches and tags to Gitee on
`main` pushes, `v*` tags, and manual workflow dispatch.

Maintainer setup:

- Add `GITEE_SSH_PRIVATE_KEY` as a GitHub repository secret.
- Add the matching public key to the Gitee repository with push permission.
- Keep the remote path as `git@gitee.com:liuyang0508/Agent-Memory-Hub.git`.

## Verification

```bash
curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh | sh -s -- --verify-only
npm view agent-memory-hub version
brew install --cask liuyang0508/agent-memory-hub/agent-memory-hub
memory doctor
```
