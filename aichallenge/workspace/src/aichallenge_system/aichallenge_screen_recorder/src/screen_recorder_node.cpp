// Copyright 2026 Tier IV, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "screen_recorder_node.hpp"

#include <QGuiApplication>
#include <QImage>
#include <QPixmap>
#include <QScreen>

#include <chrono>
#include <ctime>
#include <filesystem>
#include <future>
#include <iomanip>
#include <memory>
#include <sstream>
#include <utility>

using std::placeholders::_1;
using std::placeholders::_2;

namespace
{
std::string nowStamp()
{
  auto t = std::time(nullptr);
  std::tm tm{};
  localtime_r(&t, &tm);
  std::ostringstream os;
  os << std::put_time(&tm, "%Y%m%d-%H%M%S");
  return os.str();
}
}  // namespace

ScreenRecorder::ScreenRecorder(int hz, QObject * parent) : QObject(parent), hz_(std::max(1, hz))
{
  timer_.setTimerType(Qt::PreciseTimer);
  timer_.setInterval(static_cast<int>(1000.0 / hz_));
  connect(&timer_, &QTimer::timeout, this, &ScreenRecorder::onTick);
}

cv::Mat ScreenRecorder::grabFrame()
{
  QScreen * screen = QGuiApplication::primaryScreen();
  if (!screen) {
    return {};
  }
  // X11 only: on Wayland grabWindow() returns a null/empty pixmap.
  QPixmap pixmap = screen->grabWindow(0);
  if (pixmap.isNull()) {
    return {};
  }
  const QImage image = pixmap.toImage().convertToFormat(QImage::Format_RGB888).rgbSwapped();
  const int w = image.width();
  const int h = image.height();
  cv::Mat tmp(
    h, w, CV_8UC3, const_cast<uchar *>(image.bits()), static_cast<size_t>(image.bytesPerLine()));
  return tmp.clone();
}

bool ScreenRecorder::start(const QString & path)
{
  if (active_) {
    emit statusChanged(true, QStringLiteral("already recording"));
    return true;
  }

  const std::string path_str = path.toStdString();
  std::filesystem::create_directories(std::filesystem::path(path_str).parent_path());

  cv::Mat sample = grabFrame();
  if (sample.empty()) {
    emit statusChanged(false, QStringLiteral("no primary screen"));
    return false;
  }
  size_ = cv::Size(sample.cols, sample.rows);

  // Known limitations: the writer fps is the nominal capture rate, so dropped frames or timer
  // jitter make playback run faster than wall clock. The mp4 index (moov atom) is only written
  // on release(), so a SIGKILL before stop() leaves an unplayable file.
  std::unique_ptr<cv::VideoWriter> writer;
  for (const char * tag : {"avc1", "mp4v"}) {
    auto candidate = std::make_unique<cv::VideoWriter>(
      path_str, cv::VideoWriter::fourcc(tag[0], tag[1], tag[2], tag[3]), hz_, size_);
    if (candidate->isOpened()) {
      writer = std::move(candidate);
      break;
    }
  }
  if (!writer) {
    emit statusChanged(false, QStringLiteral("failed to open writer: %1").arg(path));
    return false;
  }

  encoder_ = std::make_unique<EncoderWorker>(std::move(writer), size_, /*max_queue=*/30);
  encoder_->submit(std::move(sample));
  active_ = true;
  timer_.start();
  emit statusChanged(
    true, QStringLiteral("recording %1x%2 @ %3Hz -> %4")
            .arg(size_.width)
            .arg(size_.height)
            .arg(hz_)
            .arg(path));
  return true;
}

void ScreenRecorder::stop()
{
  if (!active_) {
    emit statusChanged(false, QStringLiteral("not recording"));
    return;
  }
  timer_.stop();
  std::size_t dropped = 0;
  if (encoder_) {
    dropped = encoder_->stop();
    encoder_.reset();
  }
  active_ = false;
  emit statusChanged(false, QStringLiteral("stopped (dropped frames=%1)").arg(dropped));
}

void ScreenRecorder::onTick()
{
  if (!active_ || !encoder_) {
    return;
  }
  cv::Mat frame = grabFrame();
  if (!frame.empty()) {
    encoder_->submit(std::move(frame));
  }
}

ScreenRecorderNode::ScreenRecorderNode(ScreenRecorder * recorder, rclcpp::NodeOptions options)
: rclcpp::Node("screen_recorder", options), recorder_(recorder)
{
  output_dir_ = this->declare_parameter<std::string>("output_dir", "capture");
  // Resolve relative paths against the CWD once so the log below shows where files actually go.
  output_dir_ = std::filesystem::absolute(output_dir_).string();
  prefix_ = this->declare_parameter<std::string>("filename_prefix", "cap");
  const std::string service_name =
    this->declare_parameter<std::string>("service_name", "/debug/service/capture_screen");

  service_ = this->create_service<std_srvs::srv::Trigger>(
    service_name, std::bind(&ScreenRecorderNode::onTrigger, this, _1, _2));

  RCLCPP_INFO(
    this->get_logger(), "screen_recorder ready: service=%s out_dir=%s prefix=%s",
    service_name.c_str(), output_dir_.c_str(), prefix_.c_str());
}

void ScreenRecorderNode::onTrigger(
  const std::shared_ptr<std_srvs::srv::Trigger::Request> /*req*/,
  std::shared_ptr<std_srvs::srv::Trigger::Response> res)
{
  // Run start()/stop() on the Qt thread and wait for the result, so the response reflects the
  // actual recorder state and the mp4 is fully finalized before the stop response is sent.
  if (!recorder_->active()) {
    const std::string path = output_dir_ + "/" + prefix_ + "-" + nowStamp() + ".mp4";
    const QString qpath = QString::fromStdString(path);
    res->success = callOnQtThread([this, qpath] { return recorder_->start(qpath); });
    res->message = (res->success ? "start: " : "failed to start: ") + path;
  } else {
    res->success = callOnQtThread([this] {
      recorder_->stop();
      return true;
    });
    res->message = res->success ? "stop" : "failed to stop (Qt event loop unavailable)";
  }
  RCLCPP_INFO(this->get_logger(), "%s", res->message.c_str());
}

bool ScreenRecorderNode::callOnQtThread(std::function<bool()> fn)
{
  auto promise = std::make_shared<std::promise<bool>>();
  auto future = promise->get_future();
  QMetaObject::invokeMethod(
    recorder_, [promise, fn = std::move(fn)] { promise->set_value(fn()); }, Qt::QueuedConnection);
  // Timeout guards against a stopped Qt event loop (e.g. during shutdown).
  return future.wait_for(std::chrono::seconds(5)) == std::future_status::ready && future.get();
}

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  QGuiApplication app(argc, argv);

  // hz comes from an env var rather than a ROS parameter because the recorder must be
  // constructed on the Qt main thread before the ROS node (which owns the parameters) exists.
  int hz = 10;
  if (const char * env = std::getenv("AIC_SCREEN_RECORDER_HZ")) {
    try {
      hz = std::max(1, std::stoi(env));
    } catch (...) {
      hz = 10;
    }
  }

  ScreenRecorder recorder(hz);
  QObject::connect(
    &recorder, &ScreenRecorder::statusChanged, [](bool active, const QString & msg) {
      std::fprintf(
        stderr, "[screen_recorder] active=%d %s\n", static_cast<int>(active), msg.toStdString().c_str());
      std::fflush(stderr);
    });

  auto node = std::make_shared<ScreenRecorderNode>(&recorder, rclcpp::NodeOptions{});

  // SIGINT path: rclcpp installs its own signal handler that calls rclcpp::shutdown().
  // Forward shutdown to Qt so app.exec() returns and the encoder gets a chance to finalize the mp4.
  rclcpp::on_shutdown([]() { QMetaObject::invokeMethod(qApp, "quit", Qt::QueuedConnection); });

  std::thread spin_thread([node]() {
    rclcpp::spin(node);
  });

  const int rc = app.exec();

  // Back on the Qt main thread; call stop() directly to release the VideoWriter (writes mp4 moov atom).
  recorder.stop();
  rclcpp::shutdown();
  if (spin_thread.joinable()) {
    spin_thread.join();
  }
  return rc;
}
