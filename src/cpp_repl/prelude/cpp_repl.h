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
// namespace. (show(x) is instantiated once per type, so for it only the
// operators known at its first use count.) A type with an operator, of its
// own, as Boost.Assign's lists have, cannot be printed as a bare expression,
// which then runs as a statement.
//
// Names: the customisation points Put and Show are capitalised, as a type
// meant to be specialised; everything else is lower case.

#pragma once

#include <iostream>

#include <algorithm>
#include <any>
#include <atomic>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <functional>
#include <iomanip>
#include <iterator>
#include <memory>
#include <optional>
#include <limits>
#include <ostream>
#include <streambuf>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <typeinfo>
#include <utility>
#include <variant>
#include <vector>
#if __has_include(<expected>)
#include <expected>
#endif
#if defined(__linux__)
#include <sys/prctl.h>   // cpp-repl's first input turns core dumps off
#endif
#if __has_include(<cxxabi.h>)
#include <cxxabi.h>   // the name of an uncaught exception's type
#endif

namespace repl {
namespace detail {

// A stream buffer that reads a C FILE one character at a time, so that
// std::cin and C's stdin, reading the same FILE, stay in step. (libstdc++
// has one of its own; this one works with libc++ as well.)
class stdio_streambuf : public std::streambuf {
 public:
  explicit stdio_streambuf(std::FILE *file) : file_(file) {}

 protected:
  int_type underflow() override
  {
    const int c = std::getc(file_);
    if (c == EOF)
      return traits_type::eof();
    std::ungetc(c, file_);
    return c;
  }

  int_type uflow() override
  {
    const int c = std::getc(file_);
    return c == EOF ? traits_type::eof() : c;
  }

  int_type pbackfail(int_type c) override
  {
    if (traits_type::eq_int_type(c, traits_type::eof()))
      return traits_type::eof();
    return std::ungetc(traits_type::to_char_type(c), file_) == EOF ? traits_type::eof() : c;
  }

  std::streamsize xsgetn(char *s, std::streamsize n) override
  {
    return static_cast<std::streamsize>(std::fread(s, 1, static_cast<std::size_t>(n), file_));
  }

 private:
  std::FILE *file_;
};

}  // namespace detail

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
  static detail::stdio_streambuf buffer(in);
  std::cin.rdbuf(&buffer);
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

// The character types other than char: their strings print with a prefix.
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
inline constexpr bool is_std_wide_string = false;
template <class C, class Traits, class Alloc>
  requires is_char<C>
inline constexpr bool is_std_wide_string<std::basic_string<C, Traits, Alloc>> = true;
template <class C, class Traits>
  requires is_char<C>
inline constexpr bool is_std_wide_string<std::basic_string_view<C, Traits>> = true;

// A string of wchar_t, char8_t, char16_t or char32_t: a pointer to one, an
// array of them, or a standard string or string view of them.
template <class T>
inline constexpr bool is_wide_string =
    (std::is_pointer_v<T> && is_char<std::remove_cv_t<std::remove_pointer_t<T>>>) ||
    (std::is_array_v<T> && is_char<std::remove_cv_t<std::remove_extent_t<T>>>) || is_std_wide_string<T>;

template <class T>
inline constexpr bool is_optional = false;
template <class T>
inline constexpr bool is_optional<std::optional<T>> = true;
template <class T>
inline constexpr bool is_pair = false;
template <class A, class B>
inline constexpr bool is_pair<std::pair<A, B>> = true;
template <class T>
inline constexpr bool is_tuple = false;
template <class... T>
inline constexpr bool is_tuple<std::tuple<T...>> = true;
template <class T>
inline constexpr bool is_variant = false;
template <class... T>
inline constexpr bool is_variant<std::variant<T...>> = true;
template <class T>
inline constexpr bool is_reference_wrapper = false;
template <class T>
inline constexpr bool is_reference_wrapper<std::reference_wrapper<T>> = true;
template <class T>
inline constexpr bool is_shared_ptr = false;
template <class T>
inline constexpr bool is_shared_ptr<std::shared_ptr<T>> = true;
template <class T>
inline constexpr bool is_unique_ptr = false;
template <class T, class Deleter>
inline constexpr bool is_unique_ptr<std::unique_ptr<T, Deleter>> = true;
template <class T>
inline constexpr bool is_atomic = false;
template <class T>
inline constexpr bool is_atomic<std::atomic<T>> = true;
template <class T>
inline constexpr bool is_expected = false;
#ifdef __cpp_lib_expected
template <class T, class E>
inline constexpr bool is_expected<std::expected<T, E>> = true;
#endif
#ifdef __SIZEOF_INT128__
template <class T>
inline constexpr bool is_int128 = std::is_same_v<T, __int128> || std::is_same_v<T, unsigned __int128>;
#else
template <class T>
inline constexpr bool is_int128 = false;
#endif

// The standard's wrappers that print as what they hold, before any
// operator<< of theirs (a shared_ptr's writes an address) or conversion
// (an atomic converts to its value, which the char inserters would take).
template <class T>
inline constexpr bool is_wrapper = is_atomic<T> || is_reference_wrapper<T> || is_shared_ptr<T> || is_unique_ptr<T>;

// A scalar or a string: printed by Put, never by an operator<< of the
// user's, and never shown in more than one line. An enum is not one: its
// own operator<<, even one typed later, counts.
template <class T>
inline constexpr bool is_basic = std::is_arithmetic_v<T> || std::is_pointer_v<T> || std::is_null_pointer_v<T> ||
                                 is_string<T> || is_char_array<T> || is_wide_string<T>;

// Whether T has an operator<< of its own: a non-member one that this call
// finds, by argument-dependent lookup (in the type's namespace) or in scope.
// The stream's member inserters, for bool, the integers and pointers, are
// not found this way, so a type that merely converts to one of those (a
// handle with an operator bool, an array decaying to a pointer) does not
// count, and prints as what it is instead. Tag is the input asking: the
// compiler keeps a concept's answer per argument list, and a new Tag per
// input has it look again, so an operator<< typed later counts.
template <class T, class Tag>
concept streamable = requires (std::ostream &os, const T &x) { operator<<(os, x); };

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
           << static_cast<int>(static_cast<unsigned char>(c)) << std::dec << std::setfill(' ');
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

// A wide string as its literal: L"...", u8"...", u"..." or U"...", ASCII as
// it is and anything else as a \x, \u or \U escape, so that it reads back.
template <class C>
void put_wide(std::ostream &os, std::basic_string_view<C> s)
{
  os << (std::is_same_v<C, wchar_t> ? "L" : std::is_same_v<C, char8_t> ? "u8" : std::is_same_v<C, char16_t> ? "u" : "U")
     << '"';
  for (const C c : s) {
    const auto unit = static_cast<std::uint32_t>(c);
    if (unit < 0x80) {
      put_char(os, static_cast<char>(unit));
    } else {
      const auto flags = os.flags();
      const auto fill = os.fill('0');
      if constexpr (std::is_same_v<C, char8_t>)
        os << "\\x" << std::hex << std::setw(2) << unit;
      else if (unit <= 0xffff)
        os << "\\u" << std::hex << std::setw(4) << unit;
      else
        os << "\\U" << std::hex << std::setw(8) << unit;
      os.fill(fill);
      os.flags(flags);
    }
  }
  os << '"';
}

// The code units of a wide string, whatever holds them.
template <class T>
auto wide_units(const T &x)
{
  if constexpr (std::is_pointer_v<T>) {
    return std::basic_string_view<std::remove_cv_t<std::remove_pointer_t<T>>>(x);
  } else if constexpr (std::is_array_v<T>) {
    constexpr std::size_t n = std::extent_v<T>;
    return std::basic_string_view<std::remove_cv_t<std::remove_extent_t<T>>>(x, n && !x[n - 1] ? n - 1 : n);
  } else {
    return std::basic_string_view<typename T::value_type>(x.data(), x.size());
  }
}

#ifdef __SIZEOF_INT128__
inline void put_int128(std::ostream &os, unsigned __int128 magnitude, bool negative)
{
  char buffer[41];   // 39 digits and a sign
  char *p = buffer + sizeof buffer;
  do {
    *--p = static_cast<char>('0' + static_cast<int>(magnitude % 10));
    magnitude /= 10;
  } while (magnitude);
  if (negative)
    *--p = '-';
  os << std::string_view(p, static_cast<std::size_t>(buffer + sizeof buffer - p));
}
#endif

// A type's name as a programmer spells it, from the name typeid gives:
// demangled, and std::string as such rather than as the basic_string the
// library spells out.
inline std::string demangled(const char *name)
{
  std::string out = name;
#if __has_include(<cxxabi.h>)
  int status = 0;
  const std::unique_ptr<char, void (*)(void *)> readable(abi::__cxa_demangle(name, nullptr, nullptr, &status),
                                                         std::free);
  if (status == 0 && readable)
    out = readable.get();
#endif
  for (std::string_view spelled : {"std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >",
                                   "std::__1::basic_string<char, std::char_traits<char>, std::allocator<char> >",
                                   "std::basic_string<char, std::char_traits<char>, std::allocator<char> >"})
    for (std::size_t at = out.find(spelled); at != std::string::npos; at = out.find(spelled, at))
      out.replace(at, spelled.size(), "std::string");
  return out;
}

// The shortest text that reads back as the same number. (A library without
// to_chars for the type, as libc++ lacks it for long double, prints every
// digit that could matter instead.)
template <class F>
void put_float(std::ostream &os, F x)
{
  char buffer[64];
  if constexpr (requires { std::to_chars(buffer, buffer + sizeof buffer, x); }) {
    const auto result = std::to_chars(buffer, buffer + sizeof buffer, x);
    os << std::string_view(buffer, static_cast<std::size_t>(result.ptr - buffer));
  } else {
    const auto precision = os.precision(std::numeric_limits<F>::max_digits10);
    os << x;
    os.precision(precision);
  }
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
  } else if constexpr (is_int128<T>) {
#ifdef __SIZEOF_INT128__
    const bool negative = x < 0;
    put_int128(os, negative ? -static_cast<unsigned __int128>(x) : static_cast<unsigned __int128>(x), negative);
#endif
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
  } else if constexpr (is_wide_string<T>) {
    if constexpr (std::is_pointer_v<T>) {
      if (!x) {
        os << "nullptr";
        return;
      }
    }
    put_wide(os, wide_units(x));
  } else if constexpr (std::is_null_pointer_v<T>) {
    os << "nullptr";
  } else if constexpr (std::is_same_v<T, std::nullopt_t>) {
    os << "nullopt";
  } else if constexpr (std::is_same_v<T, std::monostate>) {
    os << "monostate";
  } else if constexpr (std::is_same_v<T, std::any>) {
    os << "any(" << (x.has_value() ? demangled(x.type().name()) : std::string("empty")) << ')';
  // The standard's wrappers come before a type's own operator<<: an atomic
  // or a reference_wrapper converts to what it holds, and a shared_ptr's
  // operator<< writes an address, where the value it points to is wanted.
  } else if constexpr (is_atomic<T>) {
    put_item<Tag>(os, x.load());
  } else if constexpr (is_reference_wrapper<T>) {
    put_item<Tag>(os, x.get());
  } else if constexpr (is_shared_ptr<T> || is_unique_ptr<T>) {
    if (!x) {
      os << "nullptr";
    } else {
      os << static_cast<const void *>(x.get());
      if constexpr (requires { put_item<Tag>(os, *x); })
        put_item<Tag>((os << " -> "), *x);
    }
  } else if constexpr (is_variant<T>) {
    if (x.valueless_by_exception())
      os << "valueless";
    else
      std::visit([&os](const auto &held) { put_item<Tag>(os, held); }, x);
  } else if constexpr (is_expected<T>) {
    if (x) {
      if constexpr (std::is_void_v<typename T::value_type>)
        os << "expected()";
      else
        put_item<Tag>(os, *x);
    } else {
      os << "unexpected(";
      put_item<Tag>(os, x.error());
      os << ')';
    }
  } else if constexpr (streamable<T, Tag>) {
    os << x;   // before ranges: std::filesystem::path is both, and a range of itself
  } else if constexpr (std::is_enum_v<T>) {
    os << type_name<T>() << '(' << +static_cast<std::underlying_type_t<T>>(x) << ')';
  } else if constexpr (std::is_pointer_v<T>) {
    if (!x)
      os << "nullptr";
    else
      os << reinterpret_cast<const void *>(x);
  } else if constexpr (is_pair<T>) {
    os << '(';
    put_item<Tag>(os, x.first);
    os << ", ";
    put_item<Tag>(os, x.second);
    os << ')';
  } else if constexpr (is_tuple<T>) {
    os << '(';
    std::apply([&os](const auto &...item) {
      std::size_t i = 0;
      ((os << (i++ ? ", " : ""), put_item<Tag>(os, item)), ...);
    }, x);
    os << ')';
  } else if constexpr (is_optional<T>) {
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
  } else if constexpr (!requires { Show<V>::generic; } || !requires { Put<V>::generic; } || detail::is_basic<V>) {
    // A Show or Put specialisation, a profile's, comes before the type's
    // own operator<<: the profile chose how the type reads.
    Show<V>::show(value);
  } else if constexpr (detail::streamable<V, Tag> && !std::is_enum_v<V> && !detail::is_wrapper<V>) {
    // Checked here, once per input: an operator<< typed after the first
    // print counts. (An enum's does too, through put_item, which puts the
    // standard's own wrappers first.)
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
  constexpr std::string_view units[] = {"s", "ms", "\xc2\xb5s", "ns"};
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
