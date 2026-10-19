# Contributing

Changes land through GitHub, the same way for everyone, the author included.

1. **An issue first.** Open one from the forms (bug, feature or task), or
   with `make issue`; it goes onto the project board. The title is one
   sentence that states the fact.
2. **A branch per issue.** With push access, `gh issue develop N --checkout`
   makes it, named `N-slug` and linked to the issue; from a fork, any name.
3. **`make check` before the pull request.** Every test runs against clang-repl
   23 or newer, then `claude plugin validate`; CI runs the same on the pull
   request, and that run is the one that counts.
4. **A pull request that closes the issue.** `Closes #N` in its description,
   or CI fails it; `make pr` does steps 3 and 4 for a branch named `N-slug`.
   Merges are rebases: keep commits that each say what they changed, as
   `<area>: <what is now true>`, like the history around them.

[AGENTS.md](AGENTS.md) has the layout, the commands and the rule in full.
