// stamp.hpp: wall-clock stamps that time a cpp-repl session cell by cell.
//
// A file-scope `double t = bench_stamp("name");` runs when its cell runs in
// cpp-repl, and during static initialisation in a compiled program. Each stamp
// prints a BENCH line holding CLOCK_MONOTONIC seconds, the clock that Python's
// time.monotonic() reads, so bench/run.py can line the stamps up with the
// moment it launched the process.

#pragma once

#include <cstdio>
#include <ctime>

using bench_kernel_fn = void (*)(long n, const double *A, const double *B, double *C);

inline double bench_now()
{
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return double(ts.tv_sec) + 1e-9 * double(ts.tv_nsec);
}

// The stamp of a cell that defines nothing new.
inline double bench_stamp(const char *name)
{
  const double t = bench_now();
  std::printf("BENCH {\"type\":\"stamp\",\"name\":\"%s\",\"t\":%.6f}\n", name, t);
  std::fflush(stdout);
  return t;
}

// The stamp of the cell after a kernel's definition. Naming the kernel here
// makes the JIT generate its machine code while it links this cell, before
// the stamp is read, so the code generation lands in this cell's time.
inline double bench_stamp(const char *name, bench_kernel_fn kernel)
{
  const double t = bench_now();
  std::printf("BENCH {\"type\":\"stamp\",\"name\":\"%s\",\"t\":%.6f,\"kernel\":\"%p\"}\n", name, t,
              reinterpret_cast<const void *>(kernel));
  std::fflush(stdout);
  return t;
}
