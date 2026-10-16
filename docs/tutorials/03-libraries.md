# Libraries and your project

<!-- clang-repl: 23 -->
<!-- requires: profile=eigen, profile=glm, command=cmake, header=zlib.h -->

This walk points cpp-repl at libraries and at a project of your own: the
built-in profiles, compiler flags, shared libraries, a build's
`compile_commands.json`, a `cpp-repl.toml`, printers for types you cannot
change, and speed.

## Built-in profiles

A profile loads a library and makes its types print. `-p eigen` loads
`<Eigen/Dense>`:

<!-- check: session -p eigen -->
```
$ cpp-repl -p eigen
c++> Eigen::Matrix2d m{{1, 2}, {3, 4}};
c++> m
Matrix<double> 2×2
    1    2
    3    4
c++> m * m.inverse()
Matrix<double> 2×2
    1    0
    0    1
c++> m.determinant()
-2
```

`-p glm` loads glm, with its matrix-transform and quaternion extensions. A
`float` prints as the shortest text that reads back as the same number:

<!-- check: session -p glm -->
```
$ cpp-repl -p glm
c++> glm::vec3 a{1, 2, 2};
c++> glm::length(a)
3
c++> glm::normalize(a)
vec3(0.33333334, 0.6666667, 0.6666667)
c++> glm::translate(glm::mat4(1.0f), a)
mat4x4
  1  0  0  1
  0  1  0  2
  0  0  1  2
  0  0  0  1
```

`-p usd` loads OpenUSD: a stage prints as its usda, a prim as its type,
properties and children. The
[openusd-tutorial walkthrough](https://github.com/farhatnadim/openusd-tutorial/blob/repl/repl/TUTORIAL.md)
explores a lesson with it. A profile finds its library through a variable
(`EIGEN_ROOT`, `GLM_ROOT`, `USD_ROOT`), pkg-config or the usual places, and
says what to set when it cannot.

## A project of your own

Here is a small header-only library, with a type that prints and one that
does not:

<!-- file: shapes/include/shapes.h -->
```cpp
#pragma once
#include <numbers>
#include <ostream>

#ifndef SHAPES_UNIT
#define SHAPES_UNIT "m"
#endif

namespace shapes {

struct Circle { double r; };
struct Square { double side; };

inline double area(Circle c) { return std::numbers::pi * c.r * c.r; }
inline double area(Square s) { return s.side * s.side; }

inline std::ostream &operator<<(std::ostream &os, Circle c) { return os << "Circle(r=" << c.r << ")"; }

}  // namespace shapes
```

Compiler flags go to clang as they are, `-I`, `-D` and the rest:

<!-- check: session -I shapes/include -D 'SHAPES_UNIT="cm"' -->
```
$ cpp-repl -I shapes/include -D SHAPES_UNIT='"cm"'
c++> #include "shapes.h"
c++> shapes::Circle c{2};
c++> c
Circle(r=2)
c++> shapes::area(c)
12.566370614359172
c++> SHAPES_UNIT
"cm"
```

### Once for the project: cpp-repl.toml

A `cpp-repl.toml` in the working directory, or a directory above it, says the
same once for a project. Its paths are relative to the file:

<!-- file: shapes/cpp-repl.toml -->
```toml
include = ["include"]
defines = ['SHAPES_UNIT="cm"']
prelude = ["repl_prelude.h"]
```

`prelude` names headers every session starts from. They are precompiled with
cpp-repl's own, so the session starts as fast. This one includes the library,
and gives `shapes::Square`, which you cannot change, a printer:

<!-- file: shapes/repl_prelude.h -->
```cpp
#pragma once
#include "cpp_repl.h"
#include "shapes.h"

namespace repl {

// One line, inside a list and on its own.
template <> struct Put<shapes::Square> {
    static void put(std::ostream &os, const shapes::Square &s) { os << "Square(side=" << s.side << ")"; }
};

}  // namespace repl
```

<!-- check: session --cwd shapes -->
```
$ cd shapes && cpp-repl
c++> shapes::Square{3}
Square(side=3)
c++> std::vector<shapes::Square>{{1}, {2}}
[Square(side=1), Square(side=2)]
c++> shapes::area(shapes::Square{3})
9
c++> SHAPES_UNIT
"cm"
```

`repl::Put<T>` is the one-line form, which a value on its own uses too;
`repl::Show<T>` adds a display of several lines, for a matrix or a table. A
specialisation goes in a prelude, since it has to come before the first print
of its type. The add-printer skill of the Claude Code plugin writes them for
you ([With Claude Code](05-claude-code.md)).

### Or from the build: compile_commands.json

A CMake build already knows the flags:

<!-- file: shapes/CMakeLists.txt -->
```cmake
cmake_minimum_required(VERSION 3.20)
project(shapes CXX)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
add_executable(demo src/demo.cpp)
target_include_directories(demo PRIVATE include)
target_compile_definitions(demo PRIVATE SHAPES_UNIT="mm")
target_compile_features(demo PRIVATE cxx_std_20)
```

<!-- file: shapes/src/demo.cpp -->
```cpp
#include <iostream>
#include "shapes.h"

int main() { std::cout << shapes::area(shapes::Circle{1}) << ' ' << SHAPES_UNIT << "^2\n"; }
```

<!-- check: sh -->
```
$ cmake -S shapes -B shapes/build > /dev/null && ls shapes/build/compile_commands.json
shapes/build/compile_commands.json
```

`--build-dir DIR` (or `--compile-commands DIR`) takes the include
directories, definitions, standard and language flags of the build's entry
for the file you load, or of its first entry:

<!-- check: session --build-dir shapes/build -->
```
$ cpp-repl --build-dir shapes/build
c++> #include "shapes.h"
c++> SHAPES_UNIT
"mm"
c++> shapes::area(shapes::Circle{1})
3.141592653589793
```

`compile_commands = "build"` in a `cpp-repl.toml` does the same. When both
are there, the TOML's own keys win over the build's.

## Shared libraries

`-l NAME` loads `libNAME.so`, found as the linker finds it; `--lib PATH`
loads one by its path. Here zlib computes a checksum:

<!-- check: session -l z -->
```
$ cpp-repl -l z
c++> #include <zlib.h>
c++> crc32(0, reinterpret_cast<const Bytef *>("hello"), 5)
907060870
```

A static archive (`.a`) cannot be loaded. A library's headers and its shared
library must come from the same build, with the same definitions, or calls
fail to link or crash. For a library with many parts, the add-library skill
works this out and checks it.

## Speed

The JIT compiles at `-O0` unless asked otherwise: quick to compile, slow to
run. `-O2` or `-O3` turns the optimiser on, and `--native` lets it use your
CPU's vector instructions. `%timeit` says which level it measured:

<!-- check: session -O2 -->
```
$ cpp-repl -O2
c++> std::vector<double> xs(1'000'000, 0.5);
c++> %timeit std::accumulate(xs.begin(), xs.end(), 0.0)
654 µs ± 3.34 µs per loop (mean ± std. dev. of 7 runs, 500 loops each; JIT at -O2)
```

With `-O3 --native`, Eigen typed at the prompt runs as fast as clang++
compiles it (the README's Speed section). The first start of each
configuration (the clang-repl, the flags, the preludes and the libraries)
precompiles the preludes, which takes a few seconds; every start after that
loads them in about half a second, and `%flags` names the precompiled header
in use.

## Next

[Scripts and agents](04-scripts-and-agents.md): cpp-repl without a terminal,
for a script or a coding agent.
