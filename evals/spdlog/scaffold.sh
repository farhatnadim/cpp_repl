#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(app CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(spdlog REQUIRED)
add_executable(app src/main.cpp)
target_link_libraries(app PRIVATE spdlog::spdlog)
EOF
cat > src/main.cpp <<'EOF'
#include <spdlog/spdlog.h>

int main()
{
  spdlog::set_level(spdlog::level::debug);
  spdlog::info("{} + {} = {}", 1, 2, 3);
}
EOF
