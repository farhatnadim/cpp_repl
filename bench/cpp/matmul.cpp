// matmul.cpp: the benchmark driver, compiled natively and run in cpp-repl
// unchanged.
//
// cpp-repl turns this file into cells: each directive and each file-scope
// declaration on its own, then main()'s body. Each `double bench_tN =
// bench_stamp(...)` runs when its cell runs, so the stamps time the REPL's
// start-up, the Eigen headers, a trivial cell, and the cell that defines the
// kernel. In a compiled program they all run during static initialisation,
// microseconds apart.
//
// The file follows three rules that keep the two runs alike. Every #if lives
// in bench.hpp, which the preprocessor sees whole. main() holds no work of
// its own, because at the prompt its variables become globals and its loops
// run inside functions of their own. And the kernel is a file-scope,
// non-inline function, so the REPL compiles Eigen's GEMM once, in the
// kernel's cell.

#include "stamp.hpp"

double bench_t0 = bench_stamp("start");

#include "bench.hpp"

double bench_t1 = bench_stamp("headers");
double bench_t2 = bench_stamp("control");

[[gnu::noinline]] void bench_kernel(long n, const double *A, const double *B, double *C)
{
  BENCH_KERNEL(n, A, B, C);
}

double bench_t3 = bench_stamp("kernel", bench_kernel);

int main()
{
  bench::run(bench_kernel);
  return 0;
}
