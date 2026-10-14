// kernel_aot.cpp: the GEMM kernel compiled ahead of time, for the arms that
// call it from cpp-repl through --lib.
//
// bench/run.py builds it with the native arm's flags plus -shared -fPIC
// -fvisibility=hidden -fvisibility-inlines-hidden into
// bench/build/libbench_kernel.so, and again with -fopenmp into
// libbench_kernel_omp.so. Only the extern "C" functions are exported, so the
// library's copies of Eigen's inline functions never stand in for a REPL
// session's own, which may be built with different flags.

#include "bench.hpp"

BENCH_EXPORT void bench_gemm_aot(long n, const double *A, const double *B, double *C)
{
  bench::eigen_gemm(n, A, B, C);
}

BENCH_EXPORT void bench_aot_set_threads(int threads)
{
  bench::set_threads(threads);
}

// The library's own build settings, as a JSON object.
BENCH_EXPORT const char *bench_aot_info()
{
  static std::string info;
  bench::Json j;
  bench::build_fields(j);
  info = j.text();
  return info.c_str();
}
