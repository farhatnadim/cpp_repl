// opencv_repl.h: an OpenCV profile's prelude, parsed after cpp_repl.h. A worked
// example for the add-printer skill, checked with OpenCV 4.6 on clang-repl 18.
//
// <opencv2/core.hpp> and <opencv2/imgproc.hpp>, and how a cv::Mat prints:
// its shape and type, then its elements in columns, the first and last three
// rows and columns past 10. OpenCV's own operator<< prints every element,
// which is why Show and Put are specialised here. Inside a list a Mat prints
// as nested brackets. A matrix expression such as A * B is evaluated first.
// A std::vector of points prints as a list: OpenCV's operator<< for it would
// print one point per line.

#pragma once

#include "cpp_repl.h"

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>

#include <algorithm>
#include <sstream>
#include <string>
#include <vector>

namespace repl {
namespace opencv {

// The rows or columns shown, -1 standing for the ones left out.
inline std::vector<int> shown(int n)
{
  if (n > 10)
    return {0, 1, 2, -1, n - 3, n - 2, n - 1};
  std::vector<int> out;
  for (int i = 0; i < n; ++i)
    out.push_back(i);
  return out;
}

inline std::string type_text(int type)
{
  static constexpr const char *depths[] = {"8U", "8S", "16U", "16S", "32S", "32F", "64F", "16F"};
  return std::string(depths[CV_MAT_DEPTH(type)]) + 'C' + std::to_string(CV_MAT_CN(type));
}

template <class T>
std::string channels(const cv::Mat &m, int r, int c)
{
  const T *p = m.ptr<T>(r) + c * m.channels();
  std::ostringstream os;
  if (m.channels() > 1)
    os << '(';
  for (int k = 0; k < m.channels(); ++k) {
    if (k)
      os << ", ";
    if constexpr (std::is_floating_point_v<T>)
      os << std::setprecision(6) << p[k];
    else
      os << +p[k];
  }
  if (m.channels() > 1)
    os << ')';
  return os.str();
}

inline std::string cell(const cv::Mat &m, int r, int c)
{
  if (r < 0 || c < 0)
    return "...";
  switch (m.depth()) {
    case CV_8U: return channels<unsigned char>(m, r, c);
    case CV_8S: return channels<signed char>(m, r, c);
    case CV_16U: return channels<unsigned short>(m, r, c);
    case CV_16S: return channels<short>(m, r, c);
    case CV_32S: return channels<int>(m, r, c);
    case CV_32F: return channels<float>(m, r, c);
    case CV_64F: return channels<double>(m, r, c);
    default: return "?";
  }
}

inline void show_mat(const cv::Mat &m)
{
  if (m.dims > 2) {
    std::cout << "Mat " << m.dims << "-d " << type_text(m.type()) << '\n' << std::flush;
    return;
  }
  std::cout << "Mat " << m.rows << "\xc3\x97" << m.cols << ' ' << type_text(m.type()) << '\n';
  const auto rows = shown(m.rows), cols = shown(m.cols);
  std::vector<std::size_t> width(cols.size(), 3);
  std::vector<std::vector<std::string>> cells;
  for (const int r : rows) {
    std::vector<std::string> line;
    for (std::size_t c = 0; c < cols.size(); ++c) {
      line.push_back(cell(m, r, cols[c]));
      width[c] = std::max(width[c], line.back().size());
    }
    cells.push_back(std::move(line));
  }
  for (const auto &line : cells) {
    for (std::size_t c = 0; c < line.size(); ++c)
      std::cout << "  " << std::setw(int(width[c])) << line[c];
    std::cout << '\n';
  }
  std::cout << std::flush;
}

inline void put_mat(std::ostream &os, const cv::Mat &m)
{
  if (m.dims > 2) {
    os << "Mat(" << m.dims << "-d " << type_text(m.type()) << ')';
    return;
  }
  os << '[';
  bool first_row = true;
  for (const int r : shown(m.rows)) {
    os << (first_row ? "[" : ", [");
    first_row = false;
    bool first = true;
    for (const int c : shown(m.cols)) {
      os << (first ? "" : ", ") << cell(m, r, c);
      first = false;
    }
    os << ']';
  }
  os << ']';
}

}  // namespace opencv

// cv::Mat and cv::Mat_<T>, which derives from it.
template <class T>
  requires std::is_base_of_v<cv::Mat, T>
struct Show<T> {
  static void show(const T &m) { opencv::show_mat(m); }
};

template <class T>
  requires std::is_base_of_v<cv::Mat, T>
struct Put<T> {
  static void put(std::ostream &os, const T &m) { opencv::put_mat(os, m); }
};

// A matrix expression, such as A * B or A.t(), prints as the Mat it makes.
template <>
struct Show<cv::MatExpr> {
  static void show(const cv::MatExpr &e) { opencv::show_mat(cv::Mat(e)); }
};

template <>
struct Put<cv::MatExpr> {
  static void put(std::ostream &os, const cv::MatExpr &e) { opencv::put_mat(os, cv::Mat(e)); }
};

// OpenCV prints a std::vector of points through a Mat, one point per line.
// As a list of points, each point is one item: [[1, 2], [3, 4]].
template <class T>
struct Put<std::vector<cv::Point_<T>>> {
  static void put(std::ostream &os, const std::vector<cv::Point_<T>> &points)
  {
    os << '[';
    std::size_t i = 0;
    for (const auto &p : points) {
      if (i == max_items) {
        os << ", ...";
        break;
      }
      os << (i++ ? ", " : "") << p;
    }
    os << ']';
  }
};

template <class T>
struct Show<std::vector<cv::Point_<T>>> {
  static void show(const std::vector<cv::Point_<T>> &points)
  {
    Put<std::vector<cv::Point_<T>>>::put(std::cout, points);
    std::cout << std::endl;
  }
};

}  // namespace repl
