"""Helper commands for the QA agent kit. Standard library only."""

__version__ = "0.1.0"

# Test levels a behaviour can be checked at.
LEVELS = ("unit", "component", "api", "integration", "e2e", "app")

# Levels whose tests live inside the service's own repository.
IN_SERVICE_LEVELS = ("unit", "component")

# What an agent can be doing when it looks up the rules that apply.
TASKS = ("map", "scaffold", "write", "heal", "review", "sync", "defect", "score")
