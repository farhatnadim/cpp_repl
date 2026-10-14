// kernels.hpp: the two GEMM kernels the benchmark times, over raw pointers.
//
// Matrices are n x n doubles in column-major order, Eigen's default:
// element (i, j) lives at p[i + j * n]. A raw-pointer interface lets the same
// kernel be called from a compiled program, from a cpp-repl cell, and from a
// shared library built with different flags, without passing Eigen objects
// across those boundaries.

#pragma once

#include <Eigen/Core>

namespace bench {

// C = A * B through Eigen's blocked, vectorised GEMM. noalias() tells Eigen
// that C does not overlap A or B, so it writes straight into C instead of
// into a temporary that is then copied.
inline void eigen_gemm(long n, const double *A, const double *B, double *C)
{
  using Matrix = Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic>;
  Eigen::Map<const Matrix, Eigen::Aligned64> a(A, n, n), b(B, n, n);
  Eigen::Map<Matrix, Eigen::Aligned64> c(C, n, n);
  c.noalias() = a * b;
}

// C = A * B as three plain loops, ordered so the innermost loop walks
// contiguous memory: for column-major storage that is j, k, i. There is no
// blocking and no hand vectorisation, so its speed is whatever the compiler
// makes of a simple loop nest.
inline void naive_gemm(long n, const double *__restrict A, const double *__restrict B,
                       double *__restrict C)
{
  for (long j = 0; j < n; ++j) {
    double *__restrict c = C + j * n;
    for (long i = 0; i < n; ++i)
      c[i] = 0.0;
    for (long k = 0; k < n; ++k) {
      const double b = B[k + j * n];
      const double *__restrict a = A + k * n;
      for (long i = 0; i < n; ++i)
        c[i] += a[i] * b;
    }
  }
}

}  // namespace bench
