// <lib>_repl.h: the <lib> profile's prelude, parsed after cpp_repl.h.
//
// The <lib> headers a session starts with, and how <lib>'s values print:
// <one sentence per type: what a bare expression shows, and how it looks
// inside a list>.
//
// cpp-repl precompiles this file once per configuration, and every session
// starts from it, before the first input. It holds declarations, inline
// functions and templates only: a global's dynamic initialiser would run as
// clang-repl starts, again in every clang-repl that replaces a crashed one,
// and a failure there stops the start. It also compiles outside the REPL, for
// programs saved with %save-cpp.

#pragma once

#include "cpp_repl.h"

#include <lib/main_header.hpp>

namespace repl {

// A value on its own: one or more lines, ending with a newline.
// template <>
// struct Show<lib::Thing> {
//   static void show(const lib::Thing &x)
//   {
//     std::cout << "Thing " << x.size() << '\n' << std::flush;
//   }
// };

// The same value inside a list: one line, no newline.
// template <>
// struct Put<lib::Thing> {
//   static void put(std::ostream &os, const lib::Thing &x) { os << "Thing(" << x.size() << ')'; }
// };

}  // namespace repl
