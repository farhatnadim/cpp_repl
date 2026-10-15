#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
OPENCV_LIBS=core source "$(dirname "${BASH_SOURCE[0]}")/../_lib/opencv_profile.sh"
cat > .cpp-repl/opencv.repl <<'EOF'
cv::Mat img = cv::Mat::eye(5, 5, CV_32F), out;
cv::GaussianBlur(img, out, cv::Size(5, 5), 0);
out.at<float>(2, 2)
EOF
