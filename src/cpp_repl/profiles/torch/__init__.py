"""libtorch: PyTorch's C++ API (tensors, autograd, torch::nn, torch::optim) in
every session, on the GPU when the install has CUDA, and how its errors and
values print.

The install is found from $TORCH_ROOT (or $LIBTORCH), then the torch package of
the active virtual or conda environment, then that of the python3 on PATH,
then the usual places. An install is a directory holding
include/torch/csrc/api/include/torch/torch.h and lib/libtorch.so: the
libtorch zip from pytorch.org, or the torch directory of a pip install, which
has the same layout. A pip install's CUDA headers, in its nvidia/*/include
packages, come with it, so that ATen's CUDA headers compile.
"""

import glob
import importlib.util
import os
import re
import subprocess
from pathlib import Path

from ...config import ConfigError, Profile

HERE = Path(__file__).resolve().parent
ROOT_VARIABLES = ('TORCH_ROOT', 'LIBTORCH')
MARKER = Path('include/torch/csrc/api/include/torch/torch.h')
CANDIDATES = ['~/libtorch', '/opt/libtorch', '/usr/local/libtorch']

# Loaded into every backend before the first input, dependencies first. The
# CUDA ones load whether or not an input calls into them: libtorch_cuda.so
# registers the CUDA kernels from its static initialisers, and without it
# torch::cuda::is_available() is false.
LIBS = ['c10', 'torch_cpu', 'c10_cuda', 'torch_cuda', 'torch']

# The pip packages whose headers ATen/cuda/CUDAContext.h includes, under
# site-packages/nvidia/.
NVIDIA = ['cuda_runtime', 'cublas', 'cusparse', 'cusolver', 'curand', 'cufft', 'cudnn', 'nvtx', 'cuda_nvrtc']

HELP = """\
  A tensor prints as libtorch prints it, values then [ CUDAFloatType{2,3} ];
  past a thousand elements, its first and last values. A torch::nn module
  prints its structure. Put a tensor on the GPU with .to(torch::kCUDA), or
  torch::randn({2, 3}, torch::kCUDA); call torch::cuda::synchronize() before
  timing GPU work. An error prints the first line of c10::Error's message;
  repl::thrown() has its backtrace. A declaration that throws ends the
  backend, and the replay runs every input again: torch::manual_seed(N) at
  the start draws the same random tensors again. Some Python names differ:
  torch.nn.functional.scaled_dot_product_attention is
  torch::scaled_dot_product_attention.
"""


def _is_root(path):
    path = Path(path).expanduser()
    return (path / MARKER).is_file() and (path / 'lib' / 'libtorch.so').is_file()


def _site_packages_torch(prefix):
    for found in sorted(glob.glob(os.path.join(prefix, 'lib', 'python3*', 'site-packages', 'torch'))):
        if _is_root(found):
            return Path(found)
    return None


def _python_torch():
    """The torch package of the Python running cpp-repl, then of the python3
    on PATH, found without importing it."""
    spec = importlib.util.find_spec('torch')
    if spec and spec.submodule_search_locations and _is_root(spec.submodule_search_locations[0]):
        return Path(spec.submodule_search_locations[0])
    try:
        out = subprocess.run(['python3', '-c', 'import importlib.util as u; s = u.find_spec("torch"); '
                              'print(s.submodule_search_locations[0] if s else "")'],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = out.stdout.strip()
    return Path(path) if out.returncode == 0 and path and _is_root(path) else None


def find_root(ctx):
    """The libtorch install for this session."""
    for name in ROOT_VARIABLES:
        value = ctx.env.get(name)
        if value:
            if not _is_root(value):
                raise ConfigError(f'${name} is {value}, which holds no {MARKER} and lib/libtorch.so')
            return Path(value).expanduser()
    for name in ('VIRTUAL_ENV', 'CONDA_PREFIX'):
        if ctx.env.get(name):
            found = _site_packages_torch(ctx.env[name])
            if found:
                return found
    found = _python_torch()
    if found:
        return found
    for candidate in CANDIDATES:
        if _is_root(candidate):
            return Path(candidate).expanduser()
    raise ConfigError('the torch profile needs libtorch: set TORCH_ROOT to the libtorch directory '
                      "(pytorch.org's libtorch zip) or to the torch directory of a pip install "
                      '(.../site-packages/torch)')


def version(root):
    """libtorch's version, with its CUDA build when the install says."""
    text = (root / 'include' / 'torch' / 'csrc' / 'api' / 'include' / 'torch' / 'version.h').read_text(
        errors='replace')
    parts = [re.search(rf'#define TORCH_VERSION_{n} (\d+)', text) for n in ('MAJOR', 'MINOR', 'PATCH')]
    number = '.'.join(m.group(1) for m in parts if m) or '?'
    for name in ('build-version', 'version.py'):   # the zip's, the wheel's
        path = root / name
        if path.is_file():
            m = re.search(r'\+(cu\d+|cpu|rocm[\d.]+)', path.read_text(errors='replace'))
            if m:
                return f'{number}+{m.group(1)}'
    return number


def cuda_includes(root):
    """The CUDA header directories for ATen's CUDA headers: a pip install's
    nvidia packages, else the CUDA toolkit's; [] when there are none."""
    nvidia = root.parent / 'nvidia'
    found = [nvidia / name / 'include' for name in NVIDIA if (nvidia / name / 'include').is_dir()]
    if found:
        return found
    for home in (os.environ.get('CUDA_HOME'), os.environ.get('CUDA_PATH'), '/usr/local/cuda'):
        if home and (Path(home) / 'include' / 'cuda_runtime_api.h').is_file():
            return [Path(home) / 'include']
    return []


def libraries(root):
    lib = root / 'lib'
    return [lib / f'lib{name}.so' for name in LIBS if (lib / f'lib{name}.so').is_file()]


def configure(ctx):
    root = find_root(ctx)
    libs = libraries(root)
    cuda = any(lib.name == 'libtorch_cuda.so' for lib in libs)
    flags = ['-isystem', str(root / 'include'), '-isystem', str(root / 'include' / 'torch' / 'csrc' / 'api' / 'include')]
    includes = cuda_includes(root) if cuda else []
    for d in includes:
        flags += ['-isystem', str(d)]
    if cuda and not includes:
        notes = ['no CUDA headers were found (set CUDA_HOME): the GPU works through torch::, '
                 'and ATen/cuda/*.h does not compile']
    else:
        notes = []
    return Profile(name='torch', flags=flags, libs=libs, prelude=[HERE / 'cpp_repl_torch.h'], help=HELP,
                   banner=f'libtorch {version(root)}' + ('' if cuda else ', CPU only'),
                   mounts=[root] + ([root.parent / 'nvidia'] if (root.parent / 'nvidia').is_dir() else []),
                   notes=notes,
                   completion=[root / 'include' / 'torch' / 'csrc' / 'api' / 'include' / 'torch'])
