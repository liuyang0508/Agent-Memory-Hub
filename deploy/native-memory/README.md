# Deployment preparation (not executed)

Use a dedicated `amh-native` service user and `/var/lib/amh-native` directory.
Install the reviewed commit in `/opt/agent-memory-hub` with `[web]` dependencies
in `.native-venv`; the service entrypoint is `amh-native`, not legacy `memory serve`.

1. Start on loopback and verify `/native/v1/health` returns `service=amh-native`.
   Separately verify `/native/v1/ready` returns 200 and `ready=true`. Readiness
   checks schema/index access and writer-lock acquisition; liveness stays up on
   a transient storage outage. Do not use readiness failure as a restart loop.
2. Create a synthetic tenant and two member invitations via the local CLI.
3. Run upload/search/delete and backup/restore against synthetic records.
4. Validate the proposed Nginx configuration before reload. Use the actual
   organization's TLS domain or IP certificate. Do not disable TLS verification.
5. Preserve the prior Nginx config and service state. The new database is separate
   from existing AMH; rollback disables this new location/service and restores
   the reviewed proxy config. Verify rollback before a real pilot.
6. Only after review deploy to the selected host. The temporary loopback/SSH
   synthetic test has been cleaned up; it was not a production deployment.
   Existing services, DNS, firewall and public endpoints were not changed.

Back up live SQLite with the SQLite backup API, not by copying only the main file
while WAL is active. Restore into an isolated database, run integrity_check and
verify tenant/identity/record counts and known recall results before trusting it.
A production restore also needs reconciliation of revocations since the backup.
It also needs client cursor/replica reconciliation: a restored server may have
an older change sequence than connected clients. The current protocol has no
automatic restore-epoch handshake. Follow the isolated re-enrollment procedure
in `docs/native-memory/operations.zh.md` before reopening the endpoint.

This is a single-node SQLite deployment, not active-active HA. Never put
independent database copies behind a load balancer or share a WAL file across
machines. Multi-node requirements and acceptance gates are documented in
`docs/native-memory/architecture.zh.md`.

This feature performs server-side text search and does not claim the old opaque
E2EE sync protocol. Use HTTPS and protect server storage/backups accordingly.
