## Summary

<!-- 1-3 bullets on what changed and why -->

## Type

- [ ] Bug fix
- [ ] Feature (within L1 scope)
- [ ] Docs
- [ ] Refactor
- [ ] Chore (CI / tooling)

## Testing

```bash
./agent_runtime_kit/hooks/test-hook.sh  # ✅ pass
./tests/schema-tenant-id-test.sh  # ✅ pass
./benchmarks/quickstart-60s.sh    # ✅ pass under 60s
```

## Strategy check

- [ ] My change is within L1 scope per [STRATEGY.md](../STRATEGY.md)
- [ ] If schema change, I updated `agent_runtime_kit/schema/memory-item.md`
- [ ] If hook/tool change, I added a unit test
