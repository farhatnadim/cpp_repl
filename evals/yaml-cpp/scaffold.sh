#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(app CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(yaml-cpp REQUIRED)
add_executable(app src/main.cpp)
target_link_libraries(app PRIVATE yaml-cpp::yaml-cpp)
EOF
cat > src/main.cpp <<'EOF'
#include <yaml-cpp/yaml.h>
#include <iostream>

int main()
{
  YAML::Node config = YAML::Load("name: demo\nsize: [640, 480]\n");
  std::cout << config["name"].as<std::string>() << ' ' << config["size"][0].as<int>() << '\n';
}
EOF
