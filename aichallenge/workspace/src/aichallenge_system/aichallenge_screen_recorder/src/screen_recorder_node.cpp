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

#include <QApplication>
#include <QDir>
#include <QGuiApplication>
#include <QImage>
#include <QPixmap>
#include <QScreen>

#include <chrono>
#include <csignal>
#include <cstdio>
#include <ctime>
#include <filesystem>
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

int envInt(const char * name, int dflt)
{
  if (const char * v = std::getenv(name)) {
    try {
      return std::stoi(v);
    } catch (...) {
    }
  }
  return dflt;
}

std::string envStr(const char * name, std::string dflt)
{
  if (const char * v = std::getenv(name); v && v[0] != '\0') {
    dflt = v;
  }
  return dflt;
}
}  // namespace

EncoderWorker::EncoderWorker(std::FILE * pipe, cv::Size size, std::size_t max_queue)
: pipe_(pipe), size_(size), max_queue_(max_queue)
{
  thread_ = std::thread(&EncoderWorker::run, this);
}

EncoderWorker::~EncoderWorker()
{
  if (!stop_.load()) {
    stop();
  }
}

void EncoderWorker::submit(cv::Mat frame)
{
  {
    std::lock_guard<std::mutex> lk(mtx_);
    if (queue_.size() >= max_queue_) {
      ++dropped_;
      return;
    }
    queue_.push_back(std::move(frame));
  }
  cv_.notify_one();
}

std::size_t EncoderWorker::stop()
{
  stop_.store(true);
  cv_.notify_all();
  if (thread_.joinable()) {
    thread_.join();
  }
  if (pipe_) {
    pclose(pipe_);
    pipe_ = nullptr;
  }
  return dropped_;
}

void EncoderWorker::run()
{
  while (true) {
    cv::Mat frame;
    {
      std::unique_lock<std::mutex> lk(mtx_);
      cv_.wait(lk, [this] { return stop_.load() || !queue_.empty(); });
      if (queue_.empty()) {
        if (stop_.load()) {
          break;
        }
        continue;
      }
      frame = std::move(queue_.front());
      queue_.pop_front();
    }
    if (frame.empty() || !pipe_) {
      continue;
    }
    if (frame.cols != size_.width || frame.rows != size_.height) {
      cv::Mat resized;
      cv::resize(frame, resized, size_);
      frame = std::move(resized);
    }
    if (!frame.isContinuous()) {
      frame = frame.clone();
    }
    const std::size_t bytes = frame.total() * frame.elemSize();
    std::fwrite(frame.data, 1, bytes, pipe_);
  }
}

ScreenRecorder::ScreenRecorder(int hz, int crf, QString preset, QObject * parent)
: QObject(parent), hz_(std::max(1, hz)), crf_(crf), preset_(std::move(preset))
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

void ScreenRecorder::start(QString path)
{
  if (active_) {
    emit statusChanged(true, QStringLiteral("already recording"));
    return;
  }

  cv::Mat sample = grabFrame();
  if (sample.empty()) {
    emit statusChanged(false, QStringLiteral("no primary screen"));
    return;
  }

  // libx264 + yuv420p requires even dimensions.
  const int w = sample.cols & ~1;
  const int h = sample.rows & ~1;
  size_ = cv::Size(w, h);

  const std::string path_str = path.toStdString();
  std::filesystem::create_directories(std::filesystem::path(path_str).parent_path());

  std::string quoted = "'";
  for (char c : path_str) {
    if (c == '\'') {
      quoted += "'\\''";
    } else {
      quoted += c;
    }
  }
  quoted += "'";

  std::ostringstream cmd;
  cmd << "ffmpeg -y -loglevel warning"
      << " -f rawvideo -pixel_format bgr24"
      << " -video_size " << w << "x" << h << " -framerate " << hz_ << " -i -"
      << " -c:v libx264 -preset " << preset_.toStdString() << " -crf " << crf_
      << " -pix_fmt yuv420p -movflags +faststart"
      << " " << quoted;

  std::FILE * pipe = popen(cmd.str().c_str(), "w");
  if (!pipe) {
    emit statusChanged(false, QStringLiteral("failed to spawn ffmpeg"));
    return;
  }

  encoder_ = std::make_unique<EncoderWorker>(pipe, size_, /*max_queue=*/5);
  encoder_->submit(std::move(sample));
  active_ = true;
  timer_.start();
  emit statusChanged(
    true, QStringLiteral("recording %1x%2 @ %3Hz crf=%4 preset=%5 -> %6")
            .arg(w)
            .arg(h)
            .arg(hz_)
            .arg(crf_)
            .arg(preset_)
            .arg(path));
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
  if (!active_) {
    const std::string path = output_dir_ + "/" + prefix_ + "-" + nowStamp() + ".mp4";
    QMetaObject::invokeMethod(
      recorder_, "start", Qt::QueuedConnection, Q_ARG(QString, QString::fromStdString(path)));
    active_ = true;
    res->success = true;
    res->message = "start: " + path;
  } else {
    QMetaObject::invokeMethod(recorder_, "stop", Qt::QueuedConnection);
    active_ = false;
    res->success = true;
    res->message = "stop";
  }
  RCLCPP_INFO(this->get_logger(), "%s", res->message.c_str());
}

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  std::signal(SIGPIPE, SIG_IGN);
  QApplication app(argc, argv);

  const int hz = std::max(1, envInt("AIC_SCREEN_RECORDER_HZ", 10));
  const int crf = envInt("AIC_SCREEN_RECORDER_CRF", 28);
  const std::string preset = envStr("AIC_SCREEN_RECORDER_PRESET", "veryfast");

  ScreenRecorder recorder(hz, crf, QString::fromStdString(preset));
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

  // Back on the Qt main thread; call stop() directly to close the ffmpeg pipe (flushes mp4 moov atom).
  recorder.stop();
  rclcpp::shutdown();
  if (spin_thread.joinable()) {
    spin_thread.join();
  }
  return rc;
}
