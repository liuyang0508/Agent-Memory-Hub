"""CI hook: regenerate JSON Schemas from Pydantic models."""

import json
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem


def main() -> None:
    out_dir = Path(__file__).parent / "schemas"
    out_dir.mkdir(exist_ok=True)
    schema = MemoryItem.model_json_schema()
    out_path = out_dir / "memory-item.schema.json"
    out_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
