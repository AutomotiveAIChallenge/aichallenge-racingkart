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

#ifndef SCREEN_RECORDER_NODE_HPP_
#define SCREEN_RECORDER_NODE_HPP_

#include <QObject>
#include <QString>
#include <QTimer>

#include <opencv2/opencv.hpp>
#include <rclcpp/rclcpp.hpp>

#include <std_srvs/srv/trigger.hpp>

#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

class EncoderWorker
{
public:
  EncoderWorker(std::unique_ptr<cv::VideoWriter> writer, cv::Size size, std::size_t max_queue);
  ~EncoderWorker();

  void submit(cv::Mat frame);
  std::size_t stop();  // returns dropped frame count

private:
  void run();

  std::unique_ptr<cv::VideoWriter> writer_;
  cv::Size size_;
  std::size_t max_queue_;
  std::deque<cv::Mat> queue_;
  std::mutex mtx_;
  std::condition_variable cv_;
  std::atomic<bool> stop_{false};
  std::size_t dropped_{0};
  std::thread thread_;
};

class ScreenRecorder : public QObject
{
  Q_OBJECT

public:
  explicit ScreenRecorder(int hz, QObject * parent = nullptr);
  ~ScreenRecorder() override = default;

  bool active() const { return active_; }

signals:
  void statusChanged(bool active, QString message);

public slots:
  void start(QString path);
  void stop();

private slots:
  void onTick();

private:
  cv::Mat grabFrame();

  int hz_;
  cv::Size size_{};
  bool active_{false};
  std::unique_ptr<EncoderWorker> encoder_;
  QTimer timer_;
};

class ScreenRecorderNode : public rclcpp::Node
{
public:
  ScreenRecorderNode(ScreenRecorder * recorder, rclcpp::NodeOptions options);

private:
  void onTrigger(
    const std::shared_ptr<std_srvs::srv::Trigger::Request> req,
    std::shared_ptr<std_srvs::srv::Trigger::Response> res);

  ScreenRecorder * recorder_;
  std::string output_dir_;
  std::string prefix_;
  bool active_{false};
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
};

#endif  // SCREEN_RECORDER_NODE_HPP_
