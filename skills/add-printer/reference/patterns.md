# Printer patterns, worked through

Each pattern comes from a profile that runs, and the paths point at its source:
- Eigen, glm and OpenUSD from cpp_repl's built-in profiles, in
  `src/cpp_repl/profiles/` of the checkout (`../../../src/cpp_repl/profiles/`
  from this file);
- OpenCV from `examples/` next to this file, with the `.repl` session it was
  checked with.

## 1. A family of types, with expression templates: Eigen

Eigen's matrices, arrays and expressions (`A * B`) all derive from
`Eigen::DenseBase<T>`. One trait covers them, and the printer evaluates
before it prints:

```cpp
namespace eigen {
template <class T>
inline constexpr bool is_dense = std::is_base_of_v<Eigen::DenseBase<T>, T>;
}

template <class T>
  requires eigen::is_dense<T>
struct Show<T> {
  static void show(const T &x)
  {
    const auto m = x.eval();   // A * B becomes a matrix here
    // "Matrix<double> 2×2", then the elements in right-aligned columns;
    // past 10 rows or columns, the first and last three (eigen::shown).
  }
};

template <class T>
  requires eigen::is_dense<T>
struct Put<T> { /* [[1, 2], [3, 4]] */ };
```

Source: `profiles/eigen/cpp_repl_eigen.h`. The `shown(n)` helper there returns
the row or column indices to print, with -1 standing for the elided ones.
Copy it for any large 2-D value.

## 2. Exact types, families and lists: OpenUSD

- **Exact types.** `UsdPrim` has no `operator<<`, so it would print as
  `<pxr::UsdPrim>`. The profile specialises `Show<UsdPrim>`, so that a bare
  prim shows its specifier, type, authored properties and children. A stage
  prints its usda.
- **Families.** Schema classes (`UsdGeomSphere`, `UsdGeomXform`) share a base,
  `UsdSchemaBase`, and one constrained `Show` covers them all.
- **Lists.** Every USD object prints as its path inside a list
  (`[/World, /World/Ball]`), through a `Put` constrained on `UsdObject` and
  `UsdSchemaBase`.

```cpp
template <>
struct Show<UsdPrim> {
  static void show(const UsdPrim &prim) { usd::show_prim(prim); }
};

template <class T>
  requires std::is_base_of_v<UsdSchemaBase, T>
struct Show<T> { /* "UsdGeomSphere: def Sphere /World/Ball" ... */ };

template <class T>
  requires usd::is_object<T>   // derived from UsdObject or UsdSchemaBase
struct Put<T> { static void put(std::ostream &os, const T &x) { os << x.GetPath(); } };
```

The profile also prints every enum registered with `TfEnum` by its name. It
does that through a `Put` constrained on `std::is_enum_v<T>`, which falls back
to the generic `Type(N)`. That is a deliberate broad constraint: another
profile's own partial specialisation for every enum would be ambiguous with it.
An exact specialisation, as in pattern 5, always wins over it.

Source: `profiles/usd/cpp_repl_usd.h`.

## 3. A library operator<< that floods the screen: OpenCV

`cv::Mat`'s `operator<<` prints every element, which is 30,000 numbers for a
100×100 colour image. Both printers are needed:
- `Show` gives a bare `img` its shape and type, and a corner of the elements;
- `Put` keeps a `std::vector<cv::Mat>` on one line.

`cv::Mat_<T>` derives from `cv::Mat`, so one constraint covers both:

```cpp
template <class T>
  requires std::is_base_of_v<cv::Mat, T>
struct Show<T> {
  static void show(const T &m) { opencv::show_mat(m); }   // "Mat 100×100 8UC3" + 7 rows
};

template <class T>
  requires std::is_base_of_v<cv::Mat, T>
struct Put<T> {
  static void put(std::ostream &os, const T &m) { opencv::put_mat(os, m); }
};

// A * B is a cv::MatExpr, which is not a Mat: evaluate it.
template <>
struct Show<cv::MatExpr> {
  static void show(const cv::MatExpr &e) { opencv::show_mat(cv::Mat(e)); }
};
```

The same library also has an `operator<<` for `std::vector<cv::Point_<T>>`
that prints one point per line. That outranks cpp-repl's own list printing, so
the profile specialises `Show` and `Put` for that container type too.

Source: `examples/opencv_repl.h`, checked with `examples/opencv.repl`.

## 4. No operator<< at all, and class templates with value arguments: glm

glm's vector and matrix types are class templates with integer arguments
(`glm::vec<3, float, glm::packed_highp>`), and glm has no `operator<<`. Each
gets a partial specialisation:

```cpp
template <glm::length_t L, class T, glm::qualifier Q>
struct Put<glm::vec<L, T, Q>> {   // vec3(1, 2, 3); a bare vec uses the generic Show, which calls this
  static void put(std::ostream &os, const glm::vec<L, T, Q> &v);
};

template <glm::length_t C, glm::length_t R, class T, glm::qualifier Q>
struct Show<glm::mat<C, R, T, Q>> {   // "mat4x4", then 4 rows
  static void show(const glm::mat<C, R, T, Q> &m);   // glm stores columns: row r is m[c][r]
};
```

A one-line value needs only `Put`. The generic `Show` prints it, then a
newline.

Source: `profiles/glm/cpp_repl_glm.h`, checked with the checkout's
`tests/sessions/glm.session`.

## 5. Enums by name

The generic printer shows an enum as `Type(N)`. For a library enum, print the
enumerator, so that the output reads as C++ you can type back:

```cpp
template <>
struct Put<spdlog::level::level_enum> {
  static void put(std::ostream &os, spdlog::level::level_enum level)
  {
    using namespace spdlog::level;
    const char *name = nullptr;
    switch (level) {
      case trace: name = "trace"; break;
      case debug: name = "debug"; break;
      case info: name = "info"; break;
      case warn: name = "warn"; break;
      case err: name = "err"; break;
      case critical: name = "critical"; break;
      case off: name = "off"; break;
      default: break;
    }
    if (name)
      os << "spdlog::level::" << name;
    else
      os << "spdlog::level::level_enum(" << int(level) << ')';
  }
};
```

A library's own name function can stand in for the `switch`, but only where
its names match the enumerators. spdlog's `to_string_view` gives "warning"
for `warn`.

## 6. The user's own types

An `operator<<` next to the type is enough, as long as it is one line:

```cpp
struct Point { double x, y; };
inline std::ostream &operator<<(std::ostream &os, const Point &p)
{
  return os << "Point(" << p.x << ", " << p.y << ")";
}
```

Typed at the prompt, it takes effect at the next print, even for a type that
printed before. Put it in a prelude when the program saved with `%save-cpp`
should print the same.
