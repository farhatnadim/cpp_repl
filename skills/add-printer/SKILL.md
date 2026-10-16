---
name: add-printer
description: Make a C++ type print well in cpp-repl, the C++ REPL on clang-repl, when it is typed as a bare expression - a readable display on its own and a one-line form inside containers. Use this when a value at the cpp-repl prompt prints as <TypeName>, prints far too much (every pixel of an image, every element of a matrix), spreads across lines inside a std::vector, or when a cpp-repl profile's types need printers (the add-library skill sends you here). Works for library types, the user's own types, and the types of a built-in profile such as -p usd.
allowed-tools:
  - Bash(python3 "${CLAUDE_SKILL_DIR}/../add-library/scripts/check_profile.py" *)
  - Bash(cpp-repl *)
---

# Make a type print in cpp-repl

In cpp-repl, a bare expression (no semicolon) prints its value. Which printer
runs is decided at compile time, in this order:

1. **`repl::Show<T>` or `repl::Put<T>`, when a specialisation exists.** `Show`
   is the display of a value on its own, and may take several lines; `Put`
   is its one-line form, which a bare value takes too when there is no `Show`.
2. **The type's non-member `operator<<`**, found by argument-dependent lookup.
3. **The generic printer.** Numbers, strings, containers, pairs, tuples and
   optionals print as you would expect. Anything else prints as `<TypeName>`.

Inside a container, each item prints through `repl::Put<T>`: one line, no
newline. `Put<T>` also prefers the type's `operator<<`, so a multi-line
`operator<<` spreads a `std::vector` across lines.

[reference/contract.md](reference/contract.md) has the exact rules, taken from
`src/cpp_repl/prelude/cpp_repl.h`.

## Where printers live

Printers go in the profile's prelude header (`.cpp-repl/<lib>_repl.h`), inside
`namespace repl`, after `#include "cpp_repl.h"` and the library's headers. A
user's own types without a profile go in a prelude listed in their project's
`cpp-repl.toml` (`prelude = ["tools/repl_prelude.h"]`).

**A built-in profile's types** (`-p usd`, `-p eigen`, `-p glm`): the profile's
header belongs to cpp-repl, in `src/cpp_repl/profiles/NAME/` of the plugin's
checkout, so leave it as it is. Write the printers in a header of the user's,
such as `.cpp-repl/usd_more_repl.h`, and layer it over the profile with a TOML:

```toml
# .cpp-repl/usd_more.toml, loaded with cpp-repl -c .cpp-repl/usd_more.toml
profiles = ["usd"]
prelude = ["usd_more_repl.h"]
compile_commands = "../build"   # when the project's build supplies the install
```

A built-in profile's prelude is included before a TOML's, so the new header
sees the library's types. Its own `#include`s name what it uses. When the
printer would serve every user of the profile, offer it to cpp-repl as well,
as a pull request to its header.

There are two reasons the prelude is the place:
- A specialisation has to come before the first use of the type it names. At
  the prompt, after `x` has printed once, a new `Show<X>` fails with "explicit
  specialization after instantiation".
- A program saved with `%save-cpp` includes the prelude, so it prints the same
  way.

## Look first

With the profile loaded, print four things:
- a small value;
- a large one;
- the value inside a `std::vector`;
- `show(x)`.

check_profile.py reports the same three cases as warnings:
- **`unprintable`:** the value prints as `<TypeName>`;
- **`verbose`:** it takes more than 12 lines;
- **`vector`:** an item spreads across lines.

## Choose

| What you see | What to write |
| --- | --- |
| Your own type prints as `<T>` | An `operator<<` next to the type. cpp-repl finds it even when it is typed after the first print. |
| A library type prints as `<T>` | `Put<T>`, the one-line form. Add `Show<T>` when several lines read better: matrices, tables, trees. |
| A library's `operator<<` floods the screen | Both `Show<T>` and `Put<T>`. `Show` covers a bare value and `Put` covers an item in a container: both outrank the library's `operator<<`. |
| A handle prints as `<T>` although it converts to `bool` or to a pointer | Those conversions are not printers (reference/contract.md): a value prints through a non-member `operator<<` or a `Put<T>`. Write `Put<T>`, and `Show<T>` for a multi-line form. The usd profile's printers for `SdfHandle<S>` (`GetPrimStack()`) are an example. |
| A family of types (every matrix, every `cv::Mat_<T>`) | One constrained partial specialisation, such as `template <class T> requires std::is_base_of_v<cv::Mat, T> struct Show<T>`. |
| An expression type (`A * B` gives an expression object) | Constrain on its base or its own type, and evaluate it first: `.eval()`, `cv::Mat(expr)`. |
| An enum prints as `Type(2)` | `Put<E>` with a `switch` that writes each enumerator as it is typed (`spdlog::level::warn`). A library's display names can differ: spdlog's is "warning". |
| A library's `operator<<` for a std container of its types (OpenCV's for `std::vector<cv::Point_<T>>`) | `Show` and `Put` for that container type. |

[reference/patterns.md](reference/patterns.md) has these worked through for
Eigen, OpenUSD, OpenCV and glm.

## Rules, and why

- **Leave out the `generic` member.** Only the primary `Show` has it, and
  cpp-repl reads it as "no printer of its own".
- **Constrain on the library's own types or base classes.** A constraint on a
  broad standard trait (`std::is_enum_v<T>`, `std::is_arithmetic_v<T>`, "has
  `begin()`") changes how every such type prints, whichever profiles are
  loaded with it. When two profiles' partial specialisations match one type,
  every print of that type fails as ambiguous. An exact specialisation
  (`template <> struct Put<lib::Mode>`) never collides.
- **Keep a display to about 12 lines.** A REPL value is read at a glance. Past
  10 rows or columns, show the first and last three, as the eigen profile's
  `shown()` does, and give the shape.
- **`Show` ends with a newline and a flush; `Put` writes no newline.** `Put`
  writes an item inside `[ ... ]`.
- **Print nested values with `repl::put(os, x)`.** Numbers and strings then
  print as everywhere else in cpp-repl. Type names come from
  `repl::type_name<T>()`.
- **Only declarations, inline functions and templates in the header.**
  clang-repl 18 never runs a global's dynamic initialiser in an `-include`d
  header.
- **The header must compile outside the REPL.** check_profile.py's prelude
  step and the `%save-cpp` build both compile it with clang++.

## Check

Re-run the profile's check, which compiles the prelude natively first:

```
python3 "${CLAUDE_SKILL_DIR}/../add-library/scripts/check_profile.py" \
    --config .cpp-repl/<lib>.toml --example .cpp-repl/<lib>.repl \
    --report .cpp-repl/<lib>.check.json --runs 1
```

Make sure the example prints every type you wrote a printer for, both on its
own and inside a `std::vector`. When the warnings are gone and the output
reads well, update the golden transcript with
`--golden .cpp-repl/<lib>.session --update-golden`.
