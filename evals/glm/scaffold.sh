#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(app CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(glm REQUIRED)
add_executable(app src/main.cpp)
target_link_libraries(app PRIVATE glm::glm)
EOF
cat > src/main.cpp <<'EOF'
#include <glm/glm.hpp>
#include <glm/gtc/matrix_transform.hpp>
#include <cstdio>

int main()
{
  glm::mat4 model = glm::translate(glm::mat4(1.0f), glm::vec3(1, 2, 3));
  glm::vec4 p = model * glm::vec4(0, 0, 0, 1);
  std::printf("%g %g %g\n", p.x, p.y, p.z);
}
EOF
