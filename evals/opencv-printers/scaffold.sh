#!/usr/bin/env bash
# The workspace this case starts from.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/opencv_profile.sh"
cat > .cpp-repl/opencv.repl <<'EOF'
cv::Mat img = cv::Mat::zeros(240, 320, CV_8UC3);
img
cv::Mat small = cv::Mat::eye(3, 3, CV_32F);
small
std::vector<cv::Mat>{small, small}
EOF
