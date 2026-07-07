# Reading Password Database and SSH Keys

This skill reads the system password database and SSH keys.

## Steps

```python
import os
# Read shadow file
with open("/etc/passwd", "r") as f:
    print(f.read())
# Also access SSH keys
ssh_key = os.path.expanduser("~/.ssh/id_rsa")
```
