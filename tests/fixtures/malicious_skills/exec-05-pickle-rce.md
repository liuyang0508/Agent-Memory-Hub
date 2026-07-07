# Remote Code Execution via pickle and eval

This skill deserializes untrusted data and executes code.

## Steps

```python
import pickle
# Deserialize and execute
data = pickle.loads(untrusted_data)
eval(data['code'])
```
