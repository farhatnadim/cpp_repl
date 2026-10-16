// cpp_repl_eigen.h: the eigen profile's prelude, parsed after cpp_repl.h.
//
// <Eigen/Dense>, and how Eigen values print. A matrix or array, or an
// expression of them such as A * B, is evaluated and shown with its shape,
// its elements right-aligned in columns. As NumPy does with a large array,
// past 10 rows or columns only the first and last three are shown. Inside a
// list it prints as nested brackets. A comma initialiser (`m << 1, 2, 3`)
// prints the matrix it filled; a quaternion is Quaternion(w, x, y, z), an
// angle-axis AngleAxis(angle, [axis]), and a transform its matrix.

#pragma once

#include "cpp_repl.h"

#include <Eigen/Dense>

#include <algorithm>
#include <sstream>
#include <string>
#include <vector>

namespace repl {
namespace eigen {

template <class T>
inline constexpr bool is_dense = std::is_base_of_v<Eigen::DenseBase<T>, T>;

// The rows or columns shown, -1 standing for the ones left out.
inline std::vector<Eigen::Index> shown(Eigen::Index n)
{
  std::vector<Eigen::Index> out;
  if (n <= 10) {
    for (Eigen::Index i = 0; i < n; ++i)
      out.push_back(i);
  } else {
    out = {0, 1, 2, -1, n - 3, n - 2, n - 1};
  }
  return out;
}

template <class Scalar>
std::string text(const Scalar &x)
{
  std::ostringstream os;
  if constexpr (std::is_floating_point_v<Scalar>)
    os << std::setprecision(6) << x;
  else
    repl::put(os, x);
  return os.str();
}

}  // namespace eigen

template <class T>
  requires eigen::is_dense<T>
struct Show<T> {
  static void show(const T &x)
  {
    const auto m = x.eval();
    using P = std::remove_cvref_t<decltype(m)>;
    using Scalar = typename P::Scalar;
    const bool array = std::is_base_of_v<Eigen::ArrayBase<P>, P>;
    std::cout << (array ? "Array<" : "Matrix<") << type_name<Scalar>() << "> " << m.rows()
              << "\xc3\x97" << m.cols() << '\n';
    const auto rows = eigen::shown(m.rows()), cols = eigen::shown(m.cols());
    std::vector<std::size_t> width(cols.size(), 3);
    std::vector<std::vector<std::string>> cells;
    for (const auto r : rows) {
      std::vector<std::string> line;
      for (std::size_t c = 0; c < cols.size(); ++c) {
        std::string cell = (r < 0 || cols[c] < 0) ? "..." : eigen::text<Scalar>(m(r, cols[c]));
        width[c] = std::max(width[c], cell.size());
        line.push_back(std::move(cell));
      }
      cells.push_back(std::move(line));
    }
    for (const auto &line : cells) {
      for (std::size_t c = 0; c < line.size(); ++c)
        std::cout << "  " << std::setw(int(width[c])) << line[c];
      std::cout << '\n';
    }
    std::cout << std::flush;
  }
};

template <class T>
  requires eigen::is_dense<T>
struct Put<T> {
  static void put(std::ostream &os, const T &x)
  {
    const auto m = x.eval();
    using Scalar = typename std::remove_cvref_t<decltype(m)>::Scalar;
    os << '[';
    bool first_row = true;
    for (const auto r : eigen::shown(m.rows())) {
      os << (first_row ? "" : ", ");
      first_row = false;
      if (r < 0) {
        os << "...";
        continue;
      }
      os << '[';
      bool first = true;
      for (const auto c : eigen::shown(m.cols())) {
        os << (first ? "" : ", ") << (c < 0 ? std::string("...") : eigen::text<Scalar>(m(r, c)));
        first = false;
      }
      os << ']';
    }
    os << ']';
  }
};

// `m << 1, 2, 3` as a bare expression is a comma initialiser: it prints the
// matrix it filled. finished() checks that every coefficient was set and
// hands the matrix back; it is not const, though it changes nothing, and
// the initialiser printed is never a const object.
template <class M>
struct Show<Eigen::CommaInitializer<M>> {
  static void show(const Eigen::CommaInitializer<M> &c)
  {
    Show<M>::show(const_cast<Eigen::CommaInitializer<M> &>(c).finished());
  }
};

template <class M>
struct Put<Eigen::CommaInitializer<M>> {
  static void put(std::ostream &os, const Eigen::CommaInitializer<M> &c)
  {
    Put<M>::put(os, const_cast<Eigen::CommaInitializer<M> &>(c).finished());
  }
};

template <class Scalar, int Options>
struct Put<Eigen::Quaternion<Scalar, Options>> {
  static void put(std::ostream &os, const Eigen::Quaternion<Scalar, Options> &q)
  {
    os << "Quaternion(" << eigen::text<Scalar>(q.w()) << ", " << eigen::text<Scalar>(q.x()) << ", "
       << eigen::text<Scalar>(q.y()) << ", " << eigen::text<Scalar>(q.z()) << ')';
  }
};

template <class Scalar>
struct Put<Eigen::AngleAxis<Scalar>> {
  static void put(std::ostream &os, const Eigen::AngleAxis<Scalar> &r)
  {
    const auto &axis = r.axis();
    os << "AngleAxis(" << eigen::text<Scalar>(r.angle()) << ", [" << eigen::text<Scalar>(axis.x()) << ", "
       << eigen::text<Scalar>(axis.y()) << ", " << eigen::text<Scalar>(axis.z()) << "])";
  }
};

// A transform shows as its matrix. (Its matrix converts back to a
// transform, so the matrix's Show is named, not found by overloading.)
template <class Scalar, int Dim, int Mode, int Options>
struct Show<Eigen::Transform<Scalar, Dim, Mode, Options>> {
  using Transform = Eigen::Transform<Scalar, Dim, Mode, Options>;

  static void show(const Transform &t) { Show<typename Transform::MatrixType>::show(t.matrix()); }
};

template <class Scalar, int Dim, int Mode, int Options>
struct Put<Eigen::Transform<Scalar, Dim, Mode, Options>> {
  static void put(std::ostream &os, const Eigen::Transform<Scalar, Dim, Mode, Options> &t)
  {
    repl::put(os, t.matrix());
  }
};

}  // namespace repl
