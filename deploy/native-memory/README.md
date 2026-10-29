# Deployment preparation (not executed)

Use a dedicated `amh-native` service user and `/var/lib/amh-native` directory.
Install the reviewed commit in `/opt/agent-memory-hub` with `[web]` dependencies
in `.native-venv`; the service entrypoint is `amh-native`, not legacy `memory serve`.

1. Start on loopback and verify `/native/v1/health` returns `service=amh-native`.
2. Create a synthetic tenant and two member invitations via the local CLI.
3. Run upload/search/delete and backup/restore against synthetic records.
4. Validate the proposed Nginx configuration before reload. Use the actual
   organization's TLS domain or IP certificate. Do not disable TLS verification.
5. Preserve the prior Nginx config and service state. The new database is separate
   from existing AMH; rollback disables this new location/service and restores
   the reviewed proxy config. Verify rollback before a real pilot.
6. Only after review deploy to the selected host. No server, DNS, firewall,
   existing service or public endpoint has been modified by this development work.

Back up live SQLite with the SQLite backup API, not by copying only the main file
while WAL is active. Restore into an isolated database, run integrity_check and
verify tenant/identity/record counts and known recall results before trusting it.
A production restore also needs reconciliation of revocations since the backup.

This feature performs server-side text search and does not claim the old opaque
E2EE sync protocol. Use HTTPS and protect server storage/backups accordingly.
