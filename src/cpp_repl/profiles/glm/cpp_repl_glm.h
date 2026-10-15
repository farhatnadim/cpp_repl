// cpp_repl_glm.h: the glm profile's prelude, parsed after cpp_repl.h.
//
// <glm/glm.hpp> with the matrix-transform and quaternion extensions, and how
// glm values print. A vector is vec3(1, 2, 3) and a quaternion quat(w, x, y,
// z). A matrix is shown as its rows, as written on paper (glm stores columns,
// so m[c][r] is row r, column c), its elements right-aligned in columns;
// inside a list it is nested brackets. glm has no operator<< of its own
// unless GLM_ENABLE_EXPERIMENTAL and <glm/gtx/io.hpp> are used.

#pragma once

#include "cpp_repl.h"

#include <glm/glm.hpp>
#include <glm/gtc/matrix_transform.hpp>
#include <glm/gtc/quaternion.hpp>

#include <algorithm>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace repl {

template <glm::length_t L, class T, glm::qualifier Q>
struct Put<glm::vec<L, T, Q>> {
  static void put(std::ostream &os, const glm::vec<L, T, Q> &v)
  {
    os << "vec" << L << '(';
    for (glm::length_t i = 0; i < L; ++i) {
      os << (i ? ", " : "");
      repl::put(os, v[i]);
    }
    os << ')';
  }
};

template <glm::length_t C, glm::length_t R, class T, glm::qualifier Q>
struct Put<glm::mat<C, R, T, Q>> {
  static void put(std::ostream &os, const glm::mat<C, R, T, Q> &m)
  {
    os << '[';
    for (glm::length_t r = 0; r < R; ++r) {
      os << (r ? ", [" : "[");
      for (glm::length_t c = 0; c < C; ++c) {
        os << (c ? ", " : "");
        repl::put(os, m[c][r]);
      }
      os << ']';
    }
    os << ']';
  }
};

template <glm::length_t C, glm::length_t R, class T, glm::qualifier Q>
struct Show<glm::mat<C, R, T, Q>> {
  static void show(const glm::mat<C, R, T, Q> &m)
  {
    std::cout << "mat" << C << 'x' << R << '\n';
    std::vector<std::string> cells;
    std::size_t width = 1;
    for (glm::length_t r = 0; r < R; ++r)
      for (glm::length_t c = 0; c < C; ++c) {
        std::ostringstream os;
        repl::put(os, m[c][r]);
        cells.push_back(os.str());
        width = std::max(width, cells.back().size());
      }
    for (glm::length_t r = 0; r < R; ++r) {
      for (glm::length_t c = 0; c < C; ++c)
        std::cout << "  " << std::setw(int(width)) << cells[std::size_t(r * C + c)];
      std::cout << '\n';
    }
    std::cout << std::flush;
  }
};

template <class T, glm::qualifier Q>
struct Put<glm::qua<T, Q>> {
  static void put(std::ostream &os, const glm::qua<T, Q> &q)
  {
    os << "quat(";
    repl::put(os, q.w);
    for (const T x : {q.x, q.y, q.z}) {
      os << ", ";
      repl::put(os, x);
    }
    os << ')';
  }
};

}  // namespace repl
