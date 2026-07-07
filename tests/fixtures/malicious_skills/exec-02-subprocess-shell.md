# Command Injection via subprocess with shell=True

This skill executes shell commands without proper sanitization.

## Steps

```python
import subprocess
cmd = input("Enter command: ")
subprocess.call(cmd, shell=True)
```
