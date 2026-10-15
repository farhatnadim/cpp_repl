#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
cat > src/main.cpp <<'EOF'
#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

int main()
{
  cv::Mat m = cv::Mat::eye(3, 3, CV_32F), out;
  cv::GaussianBlur(m, out, cv::Size(3, 3), 0);
}
EOF
