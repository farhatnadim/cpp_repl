# Earlier runs, not the results

- `*-0103Z.json`: the first `--quick` run (n = 256 and 1024, one round), made
  to check the REPL arms end to end. All 100 checks passed.
- `*-0106Z.*`: the first full run. 267 of its 1246 validity checks failed: the
  C++ arms wrote `sum(C)` to the JSON with 9 significant digits (`%.9g`),
  which rounds it by up to about 5e-10, above the checksum's 1e-10 tolerance.
  NumPy wrote full precision and passed. Freivalds' check passed throughout.
  The protocol counts a failed validity check as an invalid run, not as a
  result, so the format was changed to `%.17g` and the full run repeated.
  This run is kept for the record.
