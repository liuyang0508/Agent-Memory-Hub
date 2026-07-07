# Memory Exhaustion via Large List Allocation

This skill allocates excessive memory to cause OOM.

## Steps

```python
# Create massive list allocation
huge_list = [0] * 10000000  # list repeat 10000000 times
```
