#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(app CXX)
set(CMAKE_CXX_STANDARD 20)
find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs)
add_executable(app src/main.cpp)
target_link_libraries(app PRIVATE ${OpenCV_LIBS})
EOF
cat > src/main.cpp <<'EOF'
#include <opencv2/core.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

int main(int argc, char **argv)
{
  if (argc < 2)
    return 1;
  cv::Mat img = cv::imread(argv[1]), out;
  cv::GaussianBlur(img, out, cv::Size(5, 5), 0);
  return cv::imwrite("blurred.png", out) ? 0 : 1;
}
EOF
