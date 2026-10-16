# libtorch on the GPU

<!-- clang-repl: 23 -->
<!-- requires: profile=torch -->

PyTorch's C++ API, libtorch, at the prompt: tensors, a transformer block
written as a `torch::nn::Module`, a few training steps, what an error looks
like, and then the GPU: how tensors print there, the error that replays the
session, and what a timing means. Ten minutes. The `torch` profile needs
libtorch: `pip install torch` in a virtual environment (cpp-repl finds the
active one, or the one of the Python it runs with), or the libtorch zip from
pytorch.org with `TORCH_ROOT` set to its directory. A CUDA build of either
gives you the GPU; a CPU build works the same, on the CPU.

## Tensors

<!-- check: off -->

```
$ cpp-repl -p torch
cpp-repl: clang-repl 23.1.4, gnu++26, -O0, libtorch 2.9.0+cu128, in /home/you
%help lists the commands; Ctrl-D quits. Starting clang-repl...
cpp-repl: precompiling the preludes, once for this configuration (a few seconds, about ten with OpenUSD)...
c++>
```

The first start precompiles `<torch/torch.h>` with the standard-library
prelude, about fifteen seconds; a later start takes about four, most of it
the CUDA context. `torch::` is there, as `#include <torch/torch.h>` leaves
it. This session picks its device at the prompt, so that the transcripts read
the same on a machine without a GPU; on one with a GPU, every tensor's tag
says `CUDA`, as the last sections show.

<!-- check: session -p torch -->

```
c++> torch::manual_seed(0);
c++> auto device = torch::cuda::is_available() ? torch::kCUDA : torch::kCPU;
c++> auto x = torch::arange(6, torch::kFloat32).reshape({2, 3}).to(device);
c++> x.sizes()
[2, 3]
c++> x.sum().item<float>()
15
c++> (x * x).sizes()
[2, 3]
c++> x.dtype()
float
```

A bare tensor prints as libtorch prints it: its values, then a line naming
its type and shape. libtorch's own printer writes every element, so past a
thousand elements the prompt shows the first and last three with the tag, as
Python's torch does:

```
c++> torch::arange(6, torch::kFloat32).reshape({2, 3})
 0  1  2
 3  4  5
[ CPUFloatType{2,3} ]
c++> torch::arange(2000)
0  1  2  ...  1997  1998  1999  (2000 elements)
[ CPULongType{2000} ]
```

## A transformer block

A module is a `struct` deriving from `torch::nn::Module`, typed at the prompt
as it would be in a file: `register_module` gives each submodule a name, so
that `parameters()` collects them and `to(device)` moves them.

```
c++> struct Block : torch::nn::Module {
...>   Block(int64_t dim, int64_t heads)
...>       : attention(register_module("attention", torch::nn::MultiheadAttention(dim, heads))),
...>         norm(register_module("norm", torch::nn::LayerNorm(torch::nn::LayerNormOptions({dim})))),
...>         feed_forward(register_module("feed_forward", torch::nn::Linear(dim, dim))) {}
...>   torch::Tensor forward(torch::Tensor seq)
...>   {
...>     auto [attended, weights] = attention(seq, seq, seq);
...>     return norm(seq + feed_forward(attended));
...>   }
...>   torch::nn::MultiheadAttention attention;
...>   torch::nn::LayerNorm norm;
...>   torch::nn::Linear feed_forward;
...> };
c++> auto block = std::make_shared<Block>(16, 4);
c++> block->to(device);
c++> auto seq = torch::randn({5, 2, 16}, device);   // 5 steps, batch of 2, 16 features
c++> block->forward(seq).sizes()
[5, 2, 16]
c++> block->parameters().size()
8
c++> block->attention
torch::nn::MultiheadAttention(
  (out_proj): torch::nn::Linear(in_features=16, out_features=16, bias=true)
)
```

A module holder (`torch::nn::Linear`, `torch::nn::MultiheadAttention`) prints
its structure through libtorch's `operator<<`; `block` itself is a
`std::shared_ptr`, so `*block` prints the same way for your own module.

## Training steps

The loop is the one a program has. The loss is read back with `item<float>()`,
which waits for the GPU.

```
c++> torch::optim::AdamW optimizer(block->parameters(), torch::optim::AdamWOptions(1e-2));
c++> std::vector<float> losses;
c++> for (int step = 0; step < 20; ++step) {
...>   optimizer.zero_grad();
...>   auto loss = torch::mse_loss(block->forward(seq), seq);
...>   loss.backward();
...>   optimizer.step();
...>   losses.push_back(loss.item<float>());
...> }
c++> losses.size()
20
c++> losses.front() > losses.back()
true
```

## Errors

A shape mismatch throws `c10::Error`. Its `what()` is the message and a
backtrace of about thirty lines. Caught, the message alone is
`what_without_backtrace()`:

```
c++> try { torch::matmul(seq, seq); } catch (const c10::Error &e) { std::cout << e.what_without_backtrace() << '\n'; }
Expected size for first two dimensions of batch2 tensor to be: [5, 16] but got: [5, 2].
```

Uncaught, the prompt names the exception and shows the first line of its
message, and the session goes on; `repl::thrown()` holds the whole text.

<!-- check: off -->

```
c++> torch::matmul(seq, seq)
[uncaught exception: c10::Error: Expected size for first two dimensions of batch2 tensor to be: [5, 16] but got: [5, 2]. (26 more lines in repl::thrown())]
c++> repl::thrown().substr(0, 120)
"Expected size for first two dimensions of batch2 tensor to be: [5, 16] but got: [5, 2].\nException raised from common_che"
```

The one case to know about: an exception thrown while a *declaration* is
initialised, `auto bad = torch::matmul(seq, seq);`, is not caught, since the
declaration runs at file scope. It ends clang-repl, and cpp-repl rebuilds the
session by running every input again, the training loop included:

```
c++> auto bad = torch::matmul(seq, seq);
[uncaught exception: c10::Error: Expected size for first two dimensions of batch2 tensor to be: [5, 16] but got: [5, 2]. (24 more lines in repl::thrown())]
[clang-repl crashed (SIGABRT): restored 12 inputs]
c++> losses.size()
20
```

The replay draws the same random tensors, because `torch::manual_seed(0)` was
the first input. Without it, `seq` would come back different. Where a result
might throw, call it as a statement first, or declare the variable and assign
it: `torch::Tensor bad; bad = torch::matmul(seq, seq);`.

## On the GPU

With a CUDA build and a GPU, `device` is `torch::kCUDA`, and the same session
reads:

```
c++> x
 0  1  2
 3  4  5
[ CUDAFloatType{2,3} ]
c++> x.device()
cuda:0
c++> at::cuda::getCurrentDeviceProperties()->name
"NVIDIA GeForce RTX 3090"
c++> at::cuda::getCurrentDeviceProperties()->totalGlobalMem / (1 << 20)
24117
```

`<ATen/cuda/CUDAContext.h>` is in the prelude when the install's CUDA headers
were found: a pip install carries them in its `nvidia` packages, and the
libtorch zip reads them from `CUDA_HOME`.

## Timing

`%time` reports the wall time of an input and, apart from it, the time its
code ran. A CUDA kernel is launched and the call returns at once, so the
code's time of a GPU statement is the launch alone unless the statement
waits for the result, with `torch::cuda::synchronize()` or by reading a
value back:

```
c++> auto a = torch::randn({2048, 2048}, device);
c++> auto b = torch::randn({2048, 2048}, device);
c++> %time torch::matmul(a, b);
wall time: 19.9 ms, of which the code ran 48.2 µs
c++> %time torch::matmul(a, b); torch::cuda::synchronize();
wall time: 19.6 ms, of which the code ran 772 µs
c++> %timeit torch::matmul(a, b); torch::cuda::synchronize();
938 µs ± 140 µs per loop (mean ± std. dev. of 7 runs, 1 loop each; JIT at -O0, see %help)
```

The twenty milliseconds of wall time around the code are the compile of the
statement. `%timeit` runs it in a loop for a steadier figure; without the
synchronize it still reads about the same, since the launch queue fills and
each launch then waits for a kernel to finish. The JIT's `-O0` costs the C++
around the calls nothing worth noting here: the work is in the kernels.

## The program

`%save-cpp train.cpp` writes the session as a program, the module's
definition at file scope and the statements in `main()`. It builds with the
flags `cpp-repl -p torch --cflags` and `--libs` print, the libraries linked
whether or not the program calls into each, since `libtorch_cuda.so`
registers the CUDA kernels from its initialisers:

```
$ clang++ $(cpp-repl -p torch --cflags) train.cpp $(cpp-repl -p torch --libs) -o train
```

The same session is in [`examples/torch.repl`](../../examples/torch.repl):
`cpp-repl -p torch examples/torch.repl` runs it and keeps the prompt.
