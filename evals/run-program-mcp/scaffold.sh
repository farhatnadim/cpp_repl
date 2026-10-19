#!/usr/bin/env bash
# The workspace this case starts from: a program whose main() ends in
# `return 0;`, so a file run after it with --batch never runs.
source "$(dirname "${BASH_SOURCE[0]}")/../_lib/project.sh"
cat > CMakeLists.txt <<'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(stats CXX)
set(CMAKE_CXX_STANDARD 20)
add_executable(stats src/stats.cpp)
CMAKE
cat > src/stats.cpp <<'CPP'
#include <algorithm>
#include <iostream>
#include <random>
#include <vector>

int main()
{
    std::mt19937 gen(2026);
    std::normal_distribution<double> noise(10.0, 2.0);
    std::vector<double> samples(1001);
    for (double &s : samples)
        s = noise(gen);
    std::sort(samples.begin(), samples.end());
    double median = samples[samples.size() / 2];
    std::cout << "sorted " << samples.size() << " samples\n";
    return 0;
}
CPP
