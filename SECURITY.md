# Security Policy

Please do not open a public issue for suspected credential exposure, prompt-injection bypasses, path traversal, or other security-sensitive reports.

Use a private GitHub security advisory if the repository is hosted on GitHub. If private advisories are unavailable, open a minimal issue that says you have a security report without including exploit details or secrets.

When reporting, include:

- affected version or commit
- operating system and agent adapter
- minimal reproduction steps
- whether any local memory data, credentials, or filesystem paths were exposed

Agent Memory Hub stores user data locally by default under `~/.agent-memory-hub`. Do not attach real memory items, transcripts, tokens, or private repository paths unless they have been redacted.
