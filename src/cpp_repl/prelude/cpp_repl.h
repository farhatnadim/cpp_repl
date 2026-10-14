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
// first printed.

#pragma once

#include <iostream>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <iomanip>
#include <iterator>
#include <optional>
#include <ostream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>
#if defined(__linux__)
#include <sys/prctl.h>   // cpp-repl's first input turns core dumps off
#endif

namespace repl {

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

template <class T>
struct Put {
  static void put(std::ostream &os, const T &x)
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
        detail::put_char(os, x);
      os << '\'';
    } else if constexpr (detail::is_char<T>) {
      os << type_name<T>() << '(' << static_cast<unsigned long>(x) << ')';
    } else if constexpr (std::is_same_v<T, signed char> || std::is_same_v<T, unsigned char>) {
      os << static_cast<int>(x);
    } else if constexpr (std::is_floating_point_v<T>) {
      detail::put_float(os, x);
    } else if constexpr (std::is_integral_v<T>) {
      os << x;
    } else if constexpr (detail::is_string<T>) {
      if constexpr (std::is_pointer_v<T>) {
        if (!x) {
          os << "nullptr";
          return;
        }
      }
      detail::put_string(os, x);
    } else if constexpr (detail::is_char_array<T>) {
      detail::put_string(os, std::string_view(x, std::extent_v<T> && !x[std::extent_v<T> - 1]
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
    } else if constexpr (detail::is_pair<T>::value) {
      os << '(';
      repl::put(os, x.first);
      os << ", ";
      repl::put(os, x.second);
      os << ')';
    } else if constexpr (detail::is_tuple<T>::value) {
      os << '(';
      std::apply([&os](const auto &...item) {
        std::size_t i = 0;
        ((os << (i++ ? ", " : ""), repl::put(os, item)), ...);
      }, x);
      os << ')';
    } else if constexpr (detail::is_optional<T>::value) {
      if (x)
        repl::put(os, *x);
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
          repl::put(os, item.first);
          os << ": ";
          repl::put(os, item.second);
        } else {
          repl::put(os, item);
        }
      }
      os << (map || set ? '}' : ']');
    } else {
      os << '<' << type_name<T>() << '>';
    }
  }
};

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
    // Checked here, once per input, rather than in Put<V>, which is
    // instantiated once: an operator<< typed after the first print counts.
    std::cout << value << std::endl;
  } else {
    Show<V>::show(value);
  }
  return {};
}

inline void print_expr(shown) {}

template <class Tag>
void print_expr(printer<Tag>)
{
  std::cout << void_mark << std::flush;
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
