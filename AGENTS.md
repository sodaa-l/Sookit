Before making changes, read `docs/DEVELOPMENT.md` for the project's architecture, development conventions, and documented design decisions.



\- Read relevant existing code and documentation before making changes.

\- Understand existing behavior and call chains before modifying them; do not guess when something can be verified.

\- Prefer the smallest correct change. Avoid unrelated refactoring.

\- Respect documented architecture decisions and constraints. Discuss before changing them.

\- Verify third-party tool/library behavior when it affects implementation.

\- Consider relevant failure, cancellation, and close paths, not only the happy path.

\- Do not over-engineer unlikely edge cases.

\- For non-trivial changes, explain the approach and risks before implementation.

\- Verify the result after making changes.

