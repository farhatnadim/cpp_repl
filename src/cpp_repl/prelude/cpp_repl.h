// cpp_repl.h: the prelude every cpp-repl session starts from.
//
// cpp-repl passes this file to clang-repl with -include, so it is parsed once,
// before the first input. clang-repl 18 never runs code from an -include'd
// file and clang-repl 23 does, so this header holds declarations, inline
// functions and templates only: no global with a dynamic initialiser.
//
// <iostream> comes first on purpose. GCC 13's <iostream> carries module-level
// asm that clang-repl 18 cannot JIT. Parsed here, it never reaches code
// generation, and an `#include <iostream>` typed later is a no-op.
//
// The header also compiles outside the REPL, so that a session saved with
// %save-cpp builds: clang++ $(cpp-repl --cflags) saved.cpp $(cpp-repl --libs).
//
// What a bare expression prints is decided here. repl::Show<T> displays a
// value on its own and repl::Put<T> writes one inline, inside a list. Their
// primary templates cover the language and the standard library. A profile
// adds constrained partial specialisations for its library's types. Your own
// type prints through its operator<<, even one defined after the type was
// first printed, if argument-dependent lookup finds it: in the type's own
// namespace.

#pragma once

#include <iostream>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <iomanip>
#include <iterator>
#include <memory>
#include <optional>
#include <ostream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <typeinfo>
#include <utility>
#include <vector>
#if defined(__linux__)
#include <sys/prctl.h>   // cpp-repl's first input turns core dumps off
#endif
#if __has_include(<ext/stdio_sync_filebuf.h>)
#include <ext/stdio_sync_filebuf.h>   // libstdc++'s std::cin buffer, for take_stdin()
#endif
#if __has_include(<cxxabi.h>)
#include <cxxabi.h>   // the name of an uncaught exception's type
#endif

namespace repl {

// clang-repl reads the session's inputs from its stdin, so code typed at the
// prompt reads its own input from fd: take_stdin(fd), which every session runs
// first, points stdin and std::cin there. (clang-repl keeps the FILE it reads
// from, and so does the std::cin of before.)
inline void take_stdin(int fd)
{
  FILE *in = fdopen(fd, "r");
  if (!in)
    return;
  stdin = in;
#if __has_include(<ext/stdio_sync_filebuf.h>)
  static __gnu_cxx::stdio_sync_filebuf<char> buffer(in);
  std::cin.rdbuf(&buffer);
#endif
}

// The name of T as the compiler spells it.
template <class T>
std::string_view type_name()
{
  std::string_view name = __PRETTY_FUNCTION__;
  const std::size_t start = name.find("T = ") + 4;
  const std::size_t end = name.find_first_of(";]", start);
  return name.substr(start, end - start);
}

template <class T>
struct Put;    // writes one value inline: put(std::ostream &, const T &)
template <class T>
struct Show;   // displays one value on its own: show(const T &)

// Lists, maps and sets print this many items and then "...".
inline constexpr std::size_t max_items = 100;

namespace detail {

template <class T>
inline constexpr bool is_char = std::is_same_v<T, wchar_t> || std::is_same_v<T, char8_t> ||
                                std::is_same_v<T, char16_t> || std::is_same_v<T, char32_t>;
template <class T>
inline constexpr bool is_string = std::is_same_v<T, std::string> || std::is_same_v<T, std::string_view> ||
                                  std::is_same_v<T, const char *> || std::is_same_v<T, char *>;
template <class T>
inline constexpr bool is_char_array = std::is_array_v<T> &&
                                      std::is_same_v<std::remove_cv_t<std::remove_extent_t<T>>, char>;

template <class T>
struct is_optional : std::false_type {};
template <class T>
struct is_optional<std::optional<T>> : std::true_type {};
template <class T>
struct is_pair : std::false_type {};
template <class A, class B>
struct is_pair<std::pair<A, B>> : std::true_type {};
template <class T>
struct is_tuple : std::false_type {};
template <class... T>
struct is_tuple<std::tuple<T...>> : std::true_type {};

// A scalar or a string: printed by Put, never by an operator<< of the
// user's, and never shown in more than one line.
template <class T>
inline constexpr bool is_basic = std::is_arithmetic_v<T> || std::is_enum_v<T> || std::is_pointer_v<T> ||
                                 std::is_null_pointer_v<T> || is_string<T> || is_char_array<T>;

inline void put_char(std::ostream &os, char c)
{
  switch (c) {
    case '\n': os << "\\n"; break;
    case '\t': os << "\\t"; break;
    case '\r': os << "\\r"; break;
    case '\0': os << "\\0"; break;
    case '\\': os << "\\\\"; break;
    case '"':  os << "\\\""; break;
    default:
      if (static_cast<unsigned char>(c) < 0x20 || c == 0x7f)
        os << "\\x" << std::hex << std::setw(2) << std::setfill('0')
           << int(static_cast<unsigned char>(c)) << std::dec << std::setfill(' ');
      else
        os << c;
  }
}

inline void put_string(std::ostream &os, std::string_view s)
{
  os << '"';
  for (char c : s)
    put_char(os, c);
  os << '"';
}

// The shortest text that reads back as the same number.
template <class F>
void put_float(std::ostream &os, F x)
{
  char buffer[64];
  const auto result = std::to_chars(buffer, buffer + sizeof buffer, x);
  os << std::string_view(buffer, result.ptr - buffer);
}

}  // namespace detail

template <class T>
void put(std::ostream &os, const T &x)
{
  Put<T>::put(os, x);
}

namespace detail {

template <class Tag, class T>
void put_generic(std::ostream &os, const T &x);

// One value inside another, for the input Tag: through the type's Put
// specialisation if it has one, else as put_generic decides for this input.
template <class Tag, class T>
void put_item(std::ostream &os, const T &x)
{
  if constexpr (requires { Put<T>::generic; })
    put_generic<Tag>(os, x);
  else
    Put<T>::put(os, x);
}

// In the REPL, a value with no printer is written between two of these, which
// cpp-repl takes out and then says how to give the type a printer.
inline constexpr char unprintable_mark = '\x1f';

}  // namespace detail

template <class T>
struct Put {
  static constexpr bool generic = true;   // a specialisation leaves this out

  static void put(std::ostream &os, const T &x) { detail::put_generic<void>(os, x); }
};

namespace detail {

// What the primary Put writes. Tag is the input that prints, or void. Every
// input instantiates this afresh, so an operator<< declared after a type
// first printed counts in a later input, for the items of a list too.
template <class Tag, class T>
void put_generic(std::ostream &os, const T &x)
{
  if constexpr (std::is_same_v<T, bool>) {
    os << (x ? "true" : "false");
  } else if constexpr (std::is_same_v<T, char>) {
    os << '\'';
    if (x == '\'')
      os << "\\'";
    else if (x == '"')
      os << '"';
    else
      put_char(os, x);
    os << '\'';
  } else if constexpr (is_char<T>) {
    os << type_name<T>() << '(' << static_cast<unsigned long>(x) << ')';
  } else if constexpr (std::is_same_v<T, signed char> || std::is_same_v<T, unsigned char>) {
    os << static_cast<int>(x);
  } else if constexpr (std::is_floating_point_v<T>) {
    put_float(os, x);
  } else if constexpr (std::is_integral_v<T>) {
    os << x;
  } else if constexpr (is_string<T>) {
    if constexpr (std::is_pointer_v<T>) {
      if (!x) {
        os << "nullptr";
        return;
      }
    }
    put_string(os, x);
  } else if constexpr (is_char_array<T>) {
    put_string(os, std::string_view(x, std::extent_v<T> && !x[std::extent_v<T> - 1]
                                                   ? std::extent_v<T> - 1 : std::extent_v<T>));
  } else if constexpr (std::is_enum_v<T>) {
    os << type_name<T>() << '(' << +static_cast<std::underlying_type_t<T>>(x) << ')';
  } else if constexpr (std::is_null_pointer_v<T>) {
    os << "nullptr";
  } else if constexpr (std::is_pointer_v<T>) {
    if (!x)
      os << "nullptr";
    else
      os << reinterpret_cast<const void *>(x);
  } else if constexpr (requires (std::ostream &out) { out << x; }) {
    os << x;   // before ranges: std::filesystem::path is both, and a range of itself
  } else if constexpr (is_pair<T>::value) {
    os << '(';
    put_item<Tag>(os, x.first);
    os << ", ";
    put_item<Tag>(os, x.second);
    os << ')';
  } else if constexpr (is_tuple<T>::value) {
    os << '(';
    std::apply([&os](const auto &...item) {
      std::size_t i = 0;
      ((os << (i++ ? ", " : ""), put_item<Tag>(os, item)), ...);
    }, x);
    os << ')';
  } else if constexpr (is_optional<T>::value) {
    if (x)
      put_item<Tag>(os, *x);
    else
      os << "nullopt";
  } else if constexpr (requires { std::begin(x); std::end(x); }) {
    constexpr bool map = requires { typename T::key_type; typename T::mapped_type; };
    constexpr bool set = !map && requires { typename T::key_type; };
    os << (map || set ? '{' : '[');
    std::size_t i = 0;
    for (const auto &item : x) {
      if (i == max_items) {
        os << ", ...";
        break;
      }
      if (i++)
        os << ", ";
      if constexpr (map) {
        put_item<Tag>(os, item.first);
        os << ": ";
        put_item<Tag>(os, item.second);
      } else {
        put_item<Tag>(os, item);
      }
    }
    os << (map || set ? '}' : ']');
  } else {
#ifdef CPP_REPL
    os << unprintable_mark << '<' << type_name<T>() << '>' << unprintable_mark;
#else
    os << '<' << type_name<T>() << '>';
#endif
  }
}

}  // namespace detail

template <class T>
struct Show {
  static constexpr bool generic = true;   // a specialisation leaves this out

  static void show(const T &x)
  {
    Put<T>::put(std::cout, x);
    std::cout << std::endl;
  }
};

// show(x) prints x as a bare expression prints it.
template <class T>
void show(const T &x)
{
  Show<T>::show(x);
}

// cpp-repl sends a bare expression EXPR, in a function of its own, as
//   struct cpp_repl_tag; repl::print_expr(((EXPR), repl::printer<cpp_repl_tag>{}));
// The comma operator below shows the value inside the full expression, while
// the temporaries it refers to still live: `std::vector<int>{1, 2, 3}[0]`
// refers into its temporary vector, and Eigen's `R * Eigen::Vector3d(1, 0, 0)`
// to its temporary vector. A void EXPR takes the built-in comma instead, and
// print_expr receives the printer. The local struct gives every input a
// printer type of its own, so that the operator is instantiated for each.
template <class Tag>
struct printer {};

struct shown {};   // what the comma operator returns once it has shown a value

// A void expression ends its output with '\x1e', which tells cpp-repl to save
// it as a plain statement in %save-cpp. So does an expression that returns a
// stream, as `std::cout << 42` does.
inline constexpr char void_mark = '\x1e';

template <class T, class Tag>
shown operator,(T &&value, printer<Tag>)
{
  using V = std::remove_cvref_t<T>;
  if constexpr (std::is_base_of_v<std::ios_base, V>) {
    std::cout << void_mark << std::flush;
  } else if constexpr (!requires { Show<V>::generic; } || detail::is_basic<V>) {
    Show<V>::show(value);
  } else if constexpr (requires (std::ostream &os) { os << value; }) {
    // Checked here, once per input: an operator<< typed after the first
    // print counts.
    std::cout << value << std::endl;
  } else {
    detail::put_item<Tag>(std::cout, value);   // the generic Show, for this input
    std::cout << std::endl;
  }
  return {};
}

inline void print_expr(shown) {}

template <class Tag>
void print_expr(printer<Tag>)
{
  std::cout << void_mark << std::flush;
}

// A statement that does not run at top level (control flow, a lambda, a bare
// expression, an expression statement) runs as the body of a function of its
// own:
//   repl::ended cpp_repl_N() { try { STATEMENT } catch (...) { return repl::uncaught(); } return {}; }
//   repl::finish(cpp_repl_N());
// A `return EXPR;` inside it, as in main()'s `if (bad) return 1;`, converts
// EXPR to the status main() would have returned. An exception that leaves the
// statement would otherwise end the program through std::terminate(), and the
// session with it: uncaught() names it and ends the program as abort() would,
// with status 134, what a shell reports for the compiled program. finish()
// writes the status between two end marks, and cpp-repl ends the program as
// the compiled one would: --batch exits with the status, and the prompt says
// so. Nothing is rewritten and no diagnostic is turned off, so a lambda inside
// the statement that falls off its end still warns.
struct ended {
  std::optional<int> status;   // main's return value, if the statement returned
  std::string thrown;          // what an uncaught exception was, if one ended it

  ended() = default;
  ended(int value) : status(value) {}   // implicit: `return 1;` converts
};

inline constexpr char end_mark = '\x1d';
inline constexpr int abort_status = 134;   // 128 + SIGABRT

namespace detail {

inline std::string demangled(const char *name)
{
#if __has_include(<cxxabi.h>)
  int status = 0;
  const std::unique_ptr<char, void (*)(void *)> readable(abi::__cxa_demangle(name, nullptr, nullptr, &status),
                                                         std::free);
  if (status == 0 && readable)
    return readable.get();
#endif
  return name;
}

}  // namespace detail

// Inside a handler: what the exception being handled is, on one line.
inline ended uncaught()
{
  ended e(abort_status);
  try {
    throw;
  } catch (const std::exception &x) {
    e.thrown = detail::demangled(typeid(x).name()) + ": " + x.what();
  } catch (...) {
#if __has_include(<cxxabi.h>)
    if (const std::type_info *type = abi::__cxa_current_exception_type())
      e.thrown = detail::demangled(type->name());
    else
#endif
      e.thrown = "of an unknown type";
  }
  for (char &c : e.thrown)
    if (c == '\n' || c == end_mark)
      c = ' ';
  return e;
}

inline void finish(const ended &e)
{
  if (e.status) {
    // Through stdout itself, past any redirection of std::cout. An
    // exception's mark starts with '!' and carries the exception's name.
    std::fprintf(stdout, "%c%s%d%s%c\n", end_mark, e.thrown.empty() ? "" : "!", *e.status, e.thrown.c_str(),
                 end_mark);
    std::fflush(stdout);
  }
}

// keep(x) makes the compiler assume x is used, so that a computation timed
// with %timeit is not optimised away. An expression template with .eval(),
// as Eigen's are, is evaluated first.
template <class T>
inline void keep(T &&x)
{
  if constexpr (requires { x.eval(); }) {
    decltype(auto) evaluated = x.eval();
    asm volatile("" : : "g"(&evaluated) : "memory");
  } else {
    asm volatile("" : : "g"(&x) : "memory");
  }
}

namespace detail {

inline void put_seconds(std::ostream &os, double seconds)
{
  static constexpr const char *units[] = {"s", "ms", "\xc2\xb5s", "ns"};
  int unit = 0;
  while (unit < 3 && seconds != 0 && std::fabs(seconds) < 1) {
    seconds *= 1000;
    ++unit;
  }
  const auto flags = os.flags();
  const auto precision = os.precision(3);
  os << std::defaultfloat << seconds << ' ' << units[unit];
  os.precision(precision);
  os.flags(flags);
}

}  // namespace detail

// Times f as IPython's %timeit does. The number of loops per run doubles
// through 1, 2, 5, 10, 20, ... until a run takes at least 0.2 s. There are
// 7 runs, or 3 when a run takes over a second, and the result is the mean and
// standard deviation of the time per loop. opt_level is the session's -O
// level, shown because unoptimised code is easy to forget about.
template <class F>
void timeit(F f, int opt_level = -1)
{
  using clock = std::chrono::steady_clock;
  const auto seconds = [](clock::duration d) { return std::chrono::duration<double>(d).count(); };
  long loops = 1;
  double run = 0;
  for (long scale = 1;; scale *= 10) {
    for (const long step : {1L, 2L, 5L}) {
      loops = step * scale;
      const auto start = clock::now();
      for (long i = 0; i < loops; ++i)
        f();
      run = seconds(clock::now() - start);
      if (run >= 0.2 || loops >= 1000000000L)
        break;
    }
    if (run >= 0.2 || loops >= 1000000000L)
      break;
  }
  const int runs = run > 1 ? 3 : 7;
  double sum = 0, squares = 0;
  for (int r = 0; r < runs; ++r) {
    const auto start = clock::now();
    for (long i = 0; i < loops; ++i)
      f();
    const double per_loop = seconds(clock::now() - start) / loops;
    sum += per_loop;
    squares += per_loop * per_loop;
  }
  const double mean = sum / runs;
  const double spread = std::sqrt(std::max(0.0, squares / runs - mean * mean));
  detail::put_seconds(std::cout, mean);
  std::cout << " \xc2\xb1 ";
  detail::put_seconds(std::cout, spread);
  std::cout << " per loop (mean \xc2\xb1 std. dev. of " << runs << " runs, " << loops
            << (loops == 1 ? " loop" : " loops") << " each";
  if (opt_level == 0)
    std::cout << "; JIT at -O0, see %help";
  else if (opt_level > 0)
    std::cout << "; JIT at -O" << opt_level;
  std::cout << ')' << std::endl;
}

}  // namespace repl

using repl::keep;
using repl::show;
