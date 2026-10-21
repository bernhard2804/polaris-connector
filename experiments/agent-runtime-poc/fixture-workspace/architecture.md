# Fixture Architecture

The demo runtime searches project files through explicit read-only tools.
Its session state is separate from long-term memory and can be resumed.

No fixture tool may write files, invoke a shell, use secrets, or access the network.
