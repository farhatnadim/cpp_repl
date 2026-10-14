// bench.hpp: the measurement protocol shared by every C++ arm.
//
// bench/cpp/matmul.cpp includes this header and hands its kernel to
// bench::run(). The configuration comes from BENCH_* environment variables,
// which bench/run.py sets, so one binary or one REPL session serves every size
// and thread count. Each record is one line, "BENCH " followed by a JSON
// object, so the harness can pick the records out of whatever else a REPL
// prints.
//
// Every preprocessor conditional of the benchmark lives in this header.
// cpp-repl hands a header to the preprocessor whole, while it splits a .cpp
// into cells.

#pragma once

#include "kernels.hpp"
#include "stamp.hpp"

#include <Eigen/Core>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <sys/mman.h>

#ifdef _OPENMP
#include <omp.h>
#endif

// The kernel that matmul.cpp's bench_kernel() calls: Eigen's GEMM, unless the
// build asks for the plain loops (-DBENCH_NAIVE=1) or for the precompiled
// kernel in libbench_kernel.so (-DBENCH_AOT=1).
#if defined(BENCH_AOT)
#define BENCH_KERNEL bench_gemm_aot
#elif defined(BENCH_NAIVE)
#define BENCH_KERNEL bench::naive_gemm
#else
#define BENCH_KERNEL bench::eigen_gemm
#endif

#define BENCH_STR2(x) #x
#define BENCH_STR(x) BENCH_STR2(x)

// The precompiled kernel, defined in bench/cpp/kernel_aot.cpp. Every build
// declares it; only the AOT arms call it, and they load the library.
#define BENCH_EXPORT extern "C" __attribute__((visibility("default")))
BENCH_EXPORT void bench_gemm_aot(long n, const double *A, const double *B, double *C);
BENCH_EXPORT const char *bench_aot_info();
BENCH_EXPORT void bench_aot_set_threads(int threads);

namespace bench {

// ---------------------------------------------------------------------------
// Records: one JSON object per line.
// ---------------------------------------------------------------------------

inline std::string quote(const std::string &text)
{
  std::string out = "\"";
  for (const char ch : text) {
    if (ch == '"' || ch == '\\') {
      out += '\\';
      out += ch;
    } else if (static_cast<unsigned char>(ch) < 0x20) {
      char buf[8];
      std::snprintf(buf, sizeof buf, "\\u%04x", static_cast<unsigned>(ch));
      out += buf;
    } else {
      out += ch;
    }
  }
  return out + '"';
}

class Json {
 public:
  Json &str(const char *key, const std::string &value)
  {
    field(key);
    body_ += quote(value);
    return *this;
  }
  // A number with 17 significant digits, which reads back as the same
  // double (the checksum is compared at 1e-10); JSON has no NaN or infinity,
  // so those become null.
  Json &num(const char *key, double value)
  {
    field(key);
    body_ += number(value, "%.17g");
    return *this;
  }
  // A CLOCK_MONOTONIC time, to the microsecond.
  Json &time(const char *key, double value)
  {
    field(key);
    body_ += number(value, "%.6f");
    return *this;
  }
  Json &integer(const char *key, long value)
  {
    field(key);
    body_ += std::to_string(value);
    return *this;
  }
  Json &flag(const char *key, bool value)
  {
    field(key);
    body_ += value ? "true" : "false";
    return *this;
  }
  Json &raw(const char *key, const std::string &json)
  {
    field(key);
    body_ += json;
    return *this;
  }
  Json &nums(const char *key, const std::vector<double> &values)
  {
    field(key);
    body_ += '[';
    for (size_t i = 0; i < values.size(); ++i)
      body_ += (i ? "," : "") + number(values[i], "%.6e");
    body_ += ']';
    return *this;
  }
  std::string text() const { return '{' + body_ + '}'; }
  void emit() const
  {
    std::printf("BENCH %s\n", text().c_str());
    std::fflush(stdout);
  }

 private:
  void field(const char *key)
  {
    if (!body_.empty())
      body_ += ',';
    body_ += quote(key) + ':';
  }
  static std::string number(double value, const char *format)
  {
    if (!std::isfinite(value))
      return "null";
    char buf[40];
    std::snprintf(buf, sizeof buf, format, value);
    return buf;
  }
  std::string body_;
};

inline Json record(const char *type)
{
  Json j;
  j.str("type", type);
  return j;
}

// ---------------------------------------------------------------------------
// Configuration from the environment.
// ---------------------------------------------------------------------------

inline std::string env_string(const char *name, const char *fallback)
{
  const char *value = std::getenv(name);
  return value && *value ? value : fallback;
}

inline double env_double(const char *name, double fallback)
{
  const char *value = std::getenv(name);
  return value && *value ? std::strtod(value, nullptr) : fallback;
}

inline long env_long(const char *name, long fallback)
{
  const char *value = std::getenv(name);
  return value && *value ? std::strtol(value, nullptr, 10) : fallback;
}

inline std::vector<long> parse_sizes(const std::string &text)
{
  std::vector<long> sizes;
  const char *p = text.c_str();
  while (*p) {
    char *end = nullptr;
    const long n = std::strtol(p, &end, 10);
    if (end == p) {
      ++p;
      continue;
    }
    if (n > 0)
      sizes.push_back(n);
    p = end;
  }
  return sizes;
}

struct Config {
  std::string arm;
  std::vector<long> sizes;
  int threads;
  double min_time;     // sample for at least this long...
  int min_samples;     // ...and take at least this many samples,
  int max_samples;     // but stop at this many samples
  double max_time;     // or after this long.
  double sample_time;  // Each sample times enough calls to last this long.
  double warmup_time;  // Warm up for at least two calls and this long.
  double max_call;     // Skip a size predicted to take longer than this per call.
  long max_n;          // Skip sizes above this, when it is positive.
};

inline Config config()
{
  Config c;
  c.arm = env_string("BENCH_ARM", "unnamed");
  c.sizes = parse_sizes(env_string("BENCH_SIZES", "32,64,128,256,384,512,768,1024,1536,2048,3072"));
  c.threads = int(env_long("BENCH_THREADS", 1));
  c.min_time = env_double("BENCH_MIN_TIME", 1.0);
  c.min_samples = int(env_long("BENCH_MIN_SAMPLES", 5));
  c.max_samples = int(env_long("BENCH_MAX_SAMPLES", 50));
  c.max_time = env_double("BENCH_MAX_TIME", 30.0);
  c.sample_time = env_double("BENCH_SAMPLE_TIME", 0.02);
  c.warmup_time = env_double("BENCH_WARMUP_TIME", 0.25);
  c.max_call = env_double("BENCH_MAX_CALL", 10.0);
  c.max_n = env_long("BENCH_MAX_N", 0);
  return c;
}

// ---------------------------------------------------------------------------
// What this translation unit was built with. The REPL arms and the native
// arms must agree on all of it, apart from "repl".
// ---------------------------------------------------------------------------

// Eigen 5 keeps the old three macros at 3.5.0 for compatibility and spells
// its real version in EIGEN_VERSION_STRING.
inline std::string eigen_version()
{
#if defined(EIGEN_VERSION_STRING)
  return EIGEN_VERSION_STRING;
#elif defined(EIGEN_WORLD_VERSION)
  return std::to_string(EIGEN_WORLD_VERSION) + '.' + std::to_string(EIGEN_MAJOR_VERSION) + '.' +
         std::to_string(EIGEN_MINOR_VERSION);
#else
  return "unknown";
#endif
}

// The vector code paths Eigen compiled in. Eigen::SimdInstructionSetsInUse()
// prints the same text for AVX and for AVX2 with FMA, so the configuration
// checks compare these macros instead.
inline std::string eigen_vectorize()
{
  std::string out;
  auto add = [&out](const char *name) { out += (out.empty() ? "" : " ") + std::string(name); };
#ifdef EIGEN_VECTORIZE_SSE4_2
  add("SSE4.2");
#endif
#ifdef EIGEN_VECTORIZE_AVX
  add("AVX");
#endif
#ifdef EIGEN_VECTORIZE_AVX2
  add("AVX2");
#endif
#ifdef EIGEN_VECTORIZE_FMA
  add("FMA");
#endif
#ifdef EIGEN_VECTORIZE_AVX512
  add("AVX512");
#endif
  return out.empty() ? "none" : out;
}

// The x86 vector extensions the compiler was allowed to use.
inline std::string isa()
{
  std::string out;
  auto add = [&out](const char *name) { out += (out.empty() ? "" : " ") + std::string(name); };
#ifdef __SSE4_2__
  add("SSE4.2");
#endif
#ifdef __AVX__
  add("AVX");
#endif
#ifdef __AVX2__
  add("AVX2");
#endif
#ifdef __FMA__
  add("FMA");
#endif
#ifdef __AVX512F__
  add("AVX512F");
#endif
  return out.empty() ? "SSE2" : out;
}

constexpr bool ndebug()
{
#ifdef NDEBUG
  return true;
#else
  return false;
#endif
}

constexpr bool optimize()
{
#ifdef __OPTIMIZE__
  return true;
#else
  return false;
#endif
}

constexpr long openmp()
{
#ifdef _OPENMP
  return _OPENMP;
#else
  return 0;
#endif
}

constexpr bool in_repl()
{
#ifdef CPP_REPL
  return true;
#else
  return false;
#endif
}

constexpr bool uses_aot()
{
#ifdef BENCH_AOT
  return true;
#else
  return false;
#endif
}

inline Json &build_fields(Json &j)
{
  return j.str("eigen", eigen_version())
      .str("simd", Eigen::SimdInstructionSetsInUse())
      .str("vectorize", eigen_vectorize())
      .str("isa", isa())
      .flag("ndebug", ndebug())
      .flag("optimize", optimize())
      .integer("openmp", openmp())
      .integer("eigen_threads", Eigen::nbThreads())
      .str("compiler", __VERSION__)
      .integer("cplusplus", long(__cplusplus));
}

// Eigen splits a product across threads only when it was built with OpenMP;
// the precompiled kernel keeps its own setting, so it is told as well.
inline void set_threads(int threads)
{
  Eigen::setNbThreads(threads);
#ifdef _OPENMP
  omp_set_num_threads(threads);
#endif
#ifdef BENCH_AOT
  bench_aot_set_threads(threads);
#endif
}

inline void env_record(const Config &cfg)
{
  Json j = record("env");
  j.str("arm", cfg.arm).str("kernel", BENCH_STR(BENCH_KERNEL)).integer("threads", cfg.threads);
  j.flag("repl", in_repl());
  build_fields(j);
#ifdef BENCH_AOT
  j.raw("aot", bench_aot_info());
#endif
  j.emit();
}

// ---------------------------------------------------------------------------
// Data and checks.
// ---------------------------------------------------------------------------

// An n x n matrix of doubles. Buffers of 4 MB or more are aligned to 2 MB
// and offered transparent huge pages, as NumPy's allocator does; smaller
// ones are aligned to 64 bytes.
class Buffer {
 public:
  explicit Buffer(long n)
  {
    const size_t bytes = size_t(n) * size_t(n) * sizeof(double);
    const bool large = bytes >= (size_t(4) << 20);
    const size_t align = large ? (size_t(2) << 20) : 64;
    size_ = (bytes + align - 1) / align * align;
    data_ = static_cast<double *>(std::aligned_alloc(align, size_));
    if (!data_) {
      std::fprintf(stderr, "bench: cannot allocate %zu bytes\n", size_);
      std::abort();
    }
    if (large)
      madvise(data_, size_, MADV_HUGEPAGE);
  }
  ~Buffer() { std::free(data_); }
  Buffer(const Buffer &) = delete;
  Buffer &operator=(const Buffer &) = delete;
  double *data() { return data_; }

 private:
  double *data_ = nullptr;
  size_t size_ = 0;
};

// A(i, j) = ((31i + 17j) mod 97) / 97 and B(i, j) = ((7i + 13j) mod 89) / 89,
// the same matrices numpy_matmul.py builds. Every value is non-negative, so
// sum(C) does not cancel. C is zeroed, which also touches its pages before
// the first timed call.
inline void fill(long n, double *A, double *B, double *C)
{
  for (long j = 0; j < n; ++j)
    for (long i = 0; i < n; ++i) {
      A[i + j * n] = double((31 * i + 17 * j) % 97) / 97.0;
      B[i + j * n] = double((7 * i + 13 * j) % 89) / 89.0;
      C[i + j * n] = 0.0;
    }
}

// sum(C), accumulated in long double so the order of summation does not
// matter at the precision the harness compares.
inline double checksum(long n, const double *C)
{
  long double total = 0.0L;
  for (long j = 0; j < n; ++j) {
    long double column = 0.0L;
    for (long i = 0; i < n; ++i)
      column += C[i + j * n];
    total += column;
  }
  return double(total);
}

// Freivalds' check: |C v - A (B v)| / |A (B v)| for a fixed vector v. A
// wrong product leaves a residual near 1; a right one leaves rounding noise.
inline double freivalds(long n, const double *A, const double *B, const double *C)
{
  std::vector<double> v(n), bv(n, 0.0), abv(n, 0.0), cv(n, 0.0);
  for (long j = 0; j < n; ++j)
    v[j] = double((7 * j) % 13 + 1) / 13.0;
  for (long j = 0; j < n; ++j)
    for (long i = 0; i < n; ++i) {
      bv[i] += B[i + j * n] * v[j];
      cv[i] += C[i + j * n] * v[j];
    }
  for (long k = 0; k < n; ++k)
    for (long i = 0; i < n; ++i)
      abv[i] += A[i + k * n] * bv[k];
  double diff = 0.0, norm = 0.0;
  for (long i = 0; i < n; ++i) {
    diff += (cv[i] - abv[i]) * (cv[i] - abv[i]);
    norm += abv[i] * abv[i];
  }
  return std::sqrt(diff) / std::sqrt(norm);
}

inline double median(std::vector<double> values)
{
  std::sort(values.begin(), values.end());
  const size_t m = values.size() / 2;
  return values.size() % 2 ? values[m] : 0.5 * (values[m - 1] + values[m]);
}

// ---------------------------------------------------------------------------
// The protocol.
// ---------------------------------------------------------------------------

// Times kernel at size n and prints a result record; returns the median time
// of one call.
inline double measure(bench_kernel_fn kernel, long n, const Config &cfg)
{
  Buffer a(n), b(n), c(n);
  fill(n, a.data(), b.data(), c.data());

  // The first call is reported on its own: it pays for page faults, lazy
  // binding and thread start-up. Warm-up continues for at least two calls
  // and warmup_time, unless one call already exceeds max_call.
  const double t_start = bench_now();
  kernel(n, a.data(), b.data(), c.data());
  const double t_first_done = bench_now();
  const double first = t_first_done - t_start;
  const bool too_long = first > cfg.max_call;
  long calls = 1;
  while (!too_long && (calls < 2 || bench_now() - t_start < cfg.warmup_time)) {
    kernel(n, a.data(), b.data(), c.data());
    ++calls;
  }
  const double per_call = (bench_now() - t_start) / double(calls);

  // Each sample times k calls, enough to last sample_time.
  const long k = std::max(1L, long(std::ceil(cfg.sample_time / per_call)));
  std::vector<double> samples;
  const double sampling = bench_now();
  while (!too_long && int(samples.size()) < cfg.max_samples) {
    const double elapsed = bench_now() - sampling;
    if (elapsed >= cfg.max_time)
      break;
    if (int(samples.size()) >= cfg.min_samples && elapsed >= cfg.min_time)
      break;
    const double s = bench_now();
    for (long i = 0; i < k; ++i)
      kernel(n, a.data(), b.data(), c.data());
    samples.push_back((bench_now() - s) / double(k));
  }
  if (samples.empty())
    samples.push_back(first);
  const double t_end = bench_now();

  const double med = median(samples);
  Json j = record("result");
  j.str("arm", cfg.arm)
      .integer("n", n)
      .integer("threads", cfg.threads)
      .num("first_call", first)
      .integer("warmup_calls", calls)
      .integer("k", k)
      .nums("samples", samples)
      .num("median", med)
      .num("gflops", 2.0 * double(n) * double(n) * double(n) / med * 1e-9)
      .num("checksum", checksum(n, c.data()))
      .num("freivalds", freivalds(n, a.data(), b.data(), c.data()))
      .flag("too_long", too_long)
      .time("t_start", t_start)
      .time("t_first_done", t_first_done)
      .time("t_end", t_end)
      .emit();
  return med;
}

// Runs every configured size, smallest first. A size whose predicted call
// time, t(previous) * (n / previous)^3, exceeds max_call is skipped, and so
// is every size after it.
inline void run(bench_kernel_fn kernel)
{
  const Config cfg = config();
  set_threads(cfg.threads);
  env_record(cfg);
  long prev_n = 0;
  double prev_t = 0.0;
  const char *stop = nullptr;
  for (const long n : cfg.sizes) {
    double predicted = 0.0;
    if (prev_t > 0.0)
      predicted = prev_t * std::pow(double(n) / double(prev_n), 3.0);
    if (!stop && cfg.max_n > 0 && n > cfg.max_n)
      stop = "above the arm's size cap";
    if (!stop && predicted > cfg.max_call)
      stop = "predicted call time above max_call";
    if (stop) {
      record("skip").str("arm", cfg.arm).integer("n", n).str("reason", stop).num("predicted", predicted).emit();
      continue;
    }
    prev_t = measure(kernel, n, cfg);
    prev_n = n;
    if (prev_t > cfg.max_call)
      stop = "a smaller size took longer than max_call";
  }
  record("done").str("arm", cfg.arm).emit();
}

// ---------------------------------------------------------------------------
// Cells for bench/cpp/session.repl, which measures a warm REPL session.
// ---------------------------------------------------------------------------

inline long session_n() { return env_long("BENCH_SESSION_N", 512); }

inline void cell_record(const char *name, long n, double run)
{
  record("cell").str("name", name).integer("n", n).num("run", run).emit();
}

// One call of kernel at size n, timed and reported as a cell's run time.
inline double time_cell(const char *name, bench_kernel_fn kernel, long n)
{
  Buffer a(n), b(n), c(n);
  fill(n, a.data(), b.data(), c.data());
  const double t = bench_now();
  kernel(n, a.data(), b.data(), c.data());
  const double run = bench_now() - t;
  cell_record(name, n, run);
  return run;
}

// Filled matrices as Eigen maps, for an expression typed straight into a
// cell, so that the cell itself instantiates and compiles it.
class Scratch {
 public:
  using Map = Eigen::Map<Eigen::MatrixXd, Eigen::Aligned64>;
  explicit Scratch(long n) : n_(n), a_(n), b_(n), c_(n) { fill(n, a_.data(), b_.data(), c_.data()); }
  Map a() { return Map(a_.data(), n_, n_); }
  Map b() { return Map(b_.data(), n_, n_); }
  Map c() { return Map(c_.data(), n_, n_); }
  void report(const char *name, double run) const { cell_record(name, n_, run); }

 private:
  long n_;
  Buffer a_, b_, c_;
};

}  // namespace bench
