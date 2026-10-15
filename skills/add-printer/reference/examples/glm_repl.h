// glm_repl.h: a glm profile's prelude, parsed after cpp_repl.h. A worked example
// for the add-printer skill, checked with glm 0.9.9 on clang-repl 18.
//
// <glm/glm.hpp>, and how glm values print: a vector as vec3(1, 2, 3), a
// matrix as its rows (glm stores columns, so m[c][r] is row r, column c),
// and a quaternion as quat(w, x, y, z). glm has no operator<< of its own
// unless GLM_ENABLE_EXPERIMENTAL and <glm/gtx/io.hpp> are used.

#pragma once

#include "cpp_repl.h"

#include <glm/glm.hpp>
#include <glm/gtc/quaternion.hpp>

#include <algorithm>
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
