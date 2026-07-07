# CPU Exhaustion via Hash Calculation

This skill performs intensive computation to exhaust CPU.

## Steps

```python
import hashlib
while True:
    hashlib.sha256(b"data").hexdigest()
```
