# How cpp-repl prints a value

This page describes `src/cpp_repl/prelude/cpp_repl.h` in the cpp_repl
checkout, the prelude every session starts from. When it and this page
disagree, the header wins: read it.

## A bare expression

cpp-repl sends a bare expression `EXPR` (an input with no semicolon) as:

```cpp
struct cpp_repl_tag; repl::print_expr(((EXPR), repl::printer<cpp_repl_tag>{}));
```

The comma operator below runs while the temporaries in `EXPR` are still
alive. It picks a printer for `V = std::remove_cvref_t<decltype(EXPR)>`:

```cpp
template <class T, class Tag>
shown operator,(T &&value, printer<Tag>)
{
  using V = std::remove_cvref_t<T>;
  if constexpr (std::is_base_of_v<std::ios_base, V>) {
    // a stream (std::cout << 42): treated as a statement
  } else if constexpr (!requires { Show<V>::generic; } || !requires { Put<V>::generic; } || detail::is_basic<V>) {
    Show<V>::show(value);            // a Show or Put specialisation, or a number or string
  } else if constexpr (detail::streamable<V, Tag> && !std::is_enum_v<V> && !detail::is_wrapper<V>) {
    std::cout << value << std::endl; // the type's own operator<<
  } else {
    detail::put_item<Tag>(std::cout, value);   // the generic Show, for this input
    std::cout << std::endl;
  }
  return {};
}
```

What follows from this:
- **A `Show<V>` or `Put<V>` specialisation wins over the type's
  `operator<<`:** the profile chose how the type reads. The primary templates
  have a `static constexpr bool generic = true;`, and a specialisation leaves
  it out. Never add `generic` to a specialisation, of `Show` or of `Put`.
- **Only a non-member `operator<<` counts.** `detail::streamable` asks for
  `operator<<(os, x)` by name, which finds a free function, through
  argument-dependent lookup or in scope, and not the stream's member
  inserters for `bool`, the integers and pointers. So a type that merely
  converts to one of those (a handle with an `operator bool`) prints as
  `<Type>`, with a note, rather than as `1`.
- **An `operator<<` counts even when it is typed after the first print,**
  for the items of a list and for enums too. The checks run again for every
  input, which has its own `Tag` (`streamable<V, Tag>` carries it because the
  compiler keeps a concept's answer per argument list): `detail::put_item<Tag>`
  hands an item to its type's `Put` specialisation, or else to
  `detail::put_generic<Tag>`, the primary `Put`'s logic.
- **Argument-dependent lookup must find the `operator<<`:** declare it in the
  type's namespace. A global `operator<<` for `lib::T` is invisible to the
  prelude's templates, as to any template in a compiled program.
- **`void` expressions print nothing** and are saved as statements by
  `%save-cpp`.

## Show and Put

```cpp
template <class T> struct Show {   // the primary: a value on its own
  static constexpr bool generic = true;
  static void show(const T &x) { Put<T>::put(std::cout, x); std::cout << std::endl; }
};

template <class T> struct Put {    // the primary: a value inline
  static constexpr bool generic = true;
  static void put(std::ostream &os, const T &x);   // detail::put_generic<void>
};
```

The primary `Put<T>` tries these in order:
1. `bool`, then the character types, then floating-point numbers, which print
   as the shortest text that reads back as the same number;
2. integers, `__int128` included;
3. strings and character arrays, which print quoted and escaped; wide
   strings as their literal, `L"..."`, `u8"..."`, `u"..."` or `U"..."`;
4. `nullptr`, `std::nullopt`, `std::monostate`, and `std::any` as
   `any(Type)`;
5. the standard's wrappers, as what they hold: `std::atomic`,
   `std::reference_wrapper`, `std::variant`, `std::expected` (or
   `unexpected(e)`), and `std::shared_ptr`/`std::unique_ptr` as
   `0x... -> value`;
6. **the type's own `operator<<`**, a non-member one;
7. enums, which print as `Type(N)`;
8. pointers, as addresses;
9. `std::pair`, then `std::tuple`, then `std::optional` (`nullopt` when
   empty);
10. **ranges**:
   - maps as `{k: v, ...}`;
   - sets as `{a, ...}`;
   - anything else as `[a, ...]`;
   - each stops after `repl::max_items` (100) items, with `...`;
11. **anything else, as `<TypeName>`.** In the REPL, cpp-repl then notes,
   once per type, how to give it a printer.

So:
- **A container item uses its type's `operator<<`** whenever one exists, before
  any range handling. A multi-line `operator<<` therefore breaks a list across
  lines, and you need `Put<T>`.
- **A range whose items have no printer** prints as `[<Item>, <Item>]`.

## Helpers you can use

| Name | What it does |
| --- | --- |
| `repl::put(os, x)` | Writes `x` inline, through `Put`. Use it for nested values. |
| `repl::show(x)` / `show(x)` | Displays `x` the way a bare expression would. |
| `repl::type_name<T>()` | The compiler's spelling of `T`, as a `std::string_view`. |
| `repl::max_items` | 100: the most items a list prints. |
| `repl::detail::put_float(os, x)` | The shortest text that reads back as `x`. |
| `repl::detail::demangled(name)` | A `typeid(x).name()` as a programmer spells it. |

## Specialising

| Form | When |
| --- | --- |
| `template <> struct Show<lib::Image> { static void show(const lib::Image &); };` | One type exactly. |
| `template <class T> struct Put<lib::Vec<T>> { ... };` | One class template, any arguments. |
| `template <class T> requires lib::is_matrix<T> struct Show<T> { ... };` | A family that a trait or a concept recognises, such as every class derived from a base. |

A constrained partial specialisation competes with the other partial
specialisations. When two match one type and neither is more specialised,
every print of that type fails with "ambiguous partial specializations".
Constrain on something only the library's types satisfy.

## The prelude header

clang-repl parses each prelude once, with `-include`, before the first input.
So the header:
- holds **declarations, inline functions and templates only**. clang-repl 18
  never runs a global's dynamic initialiser in an `-include`d file, and
  clang-repl 23 does, so code that relies on one behaves differently.
- **includes `cpp_repl.h` first**, then the library;
- **compiles with clang++ on its own.** `%save-cpp` programs include it.
- has a **unique file name.** `%save-cpp` writes `#include "<basename>"` for
  each prelude.
