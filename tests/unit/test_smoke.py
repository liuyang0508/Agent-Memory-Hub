def test_import_agent_brain():
    import agent_brain
    # Version is bumped on every release; this assertion is a smoke test that
    # the package imports and exposes __version__, not a contract on the literal.
    assert agent_brain.__version__ == "1.1.0"
