# An OpenCV profile with no printers, for the cases that start from one.
# OPENCV_LIBS: the modules to list (default core imgproc imgcodecs).
ARCH="$(python3 -c 'import sysconfig; print(sysconfig.get_config_var("MULTIARCH") or "x86_64-linux-gnu")')"
mkdir -p .cpp-repl
{
  echo '# opencv 4.6.0 for cpp-repl. Start a session with: cpp-repl -c .cpp-repl/opencv.toml'
  echo 'flags = ["-isystem", "/usr/include/opencv4"]'
  echo 'libs = ['
  for m in ${OPENCV_LIBS:-core imgproc imgcodecs}; do
    echo "  \"/usr/lib/$ARCH/libopencv_$m.so\","
  done
  echo ']'
  echo 'prelude = ["opencv_repl.h"]'
} > .cpp-repl/opencv.toml
cat > .cpp-repl/opencv_repl.h <<'EOF'
#pragma once
#include "cpp_repl.h"
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
EOF
