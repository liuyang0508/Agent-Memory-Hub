#!/usr/bin/env python3
"""agent_runtime_kit/mcp/server.py — v1 thin shim. Delegates to agent_brain.interfaces.mcp.server.run().

Legacy implementation preserved in agent_runtime_kit/mcp/_legacy/server.py
"""
from agent_brain.interfaces.mcp.server import run

if __name__ == "__main__":
    run()
