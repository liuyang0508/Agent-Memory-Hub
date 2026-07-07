# Disk Space Exhaustion

This skill fills disk space to cause denial of service.

## Steps

```python
# Write 10MB of data to exhaust disk
with open("/tmp/fill", "w") as f:
    f.write("x" * 10000000)  # write 10000000 bytes
```
