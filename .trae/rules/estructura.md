### File size and modularization
- Files should not exceed 600 lines of code.
- When a file reaches the 600-line limit, refactor by moving logic into a dedicated module or directory (e.g., `main.rs` -> `src/main/mod.rs` and sub-modules).
- Group extracted logic by responsibility (e.g., CLI parsing, configuration, caching, protocol bridges) to maintain a flat and navigable structure.