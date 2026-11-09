# Start Reactor skill

The task controller triggers this skill when:

1. `ownTasks` contains an incomplete `StartReactor` task.
2. The player has reached `skeld.reactor.start-reactor`.
3. The plugin has opened the minigame.

The skill captures the game client, recognizes the nine blue flashes,
clicks each sequence after the panel dims, and handles all five rounds.

Entry point:

```python
from skills.start_reactor import run_skill

run_skill(
    window="Among Us",
    fps=30,
    backend="auto",
)
```

The source file is `skill.py`; it can still be run directly for
diagnostics, but normal task execution uses the package entry point.
