// cpp_repl_torch.h: the torch profile's prelude, parsed after cpp_repl.h.
//
// <torch/torch.h>, ATen's CUDA context when the CUDA headers are there, and
// how tensors print. A bare tensor prints as libtorch prints it: its values,
// then a line naming its type and shape, [ CUDAFloatType{2,3} ]. A tensor of
// more than a thousand elements prints its first and last values on one line
// with that tag, as Python's torch does with "...", since libtorch's own
// printer writes every element. Inside a list a tensor is its tag alone:
// CUDAFloatType{2,3}. Devices, sizes, dtypes and modules print through
// libtorch's own operator<<.

#pragma once

#include "cpp_repl.h"

#include <torch/torch.h>
#if __has_include(<cuda_runtime_api.h>) && __has_include(<ATen/cuda/CUDAContext.h>)
#include <ATen/cuda/CUDAContext.h>   // at::cuda::getCurrentDeviceProperties(), streams
#endif

#include <iostream>
#include <string>

namespace repl {

namespace detail {

// What libtorch writes after a tensor's values, without the brackets:
// CUDAFloatType{2,3}, or "undefined".
inline std::string tensor_tag(const at::Tensor &t)
{
  if (!t.defined())
    return "undefined";
  std::string tag = t.toString() + '{';
  for (std::size_t i = 0; i < t.sizes().size(); ++i)
    tag += (i ? "," : "") + std::to_string(t.sizes()[i]);
  return tag + '}';
}

}  // namespace detail

template <>
struct Put<at::Tensor> {
  static void put(std::ostream &os, const at::Tensor &t) { os << detail::tensor_tag(t); }
};

template <>
struct Show<at::Tensor> {
  static constexpr int64_t full = 1000;   // up to this many elements, libtorch's own print

  static void show(const at::Tensor &t)
  {
    if (!t.defined() || t.numel() <= full) {
      std::cout << t << std::endl;
      return;
    }
    const bool numeric = t.is_floating_point() || at::isIntegralType(t.scalar_type(), true);
    if (numeric) {
      // data_ptr<double>, not const_data_ptr: libtorch exports the former.
      at::Tensor flat = t.detach().flatten().to(torch::kDouble).to(torch::kCPU).contiguous();
      const double *v = flat.data_ptr<double>();
      const int64_t n = flat.numel();
      for (int64_t i = 0; i < 3; ++i)
        std::cout << (i ? "  " : "") << v[i];
      std::cout << "  ...";
      for (int64_t i = n - 3; i < n; ++i)
        std::cout << "  " << v[i];
      std::cout << "  (" << n << " elements)\n";
    }
    std::cout << "[ " << detail::tensor_tag(t) << " ]" << std::endl;
  }
};

}  // namespace repl
