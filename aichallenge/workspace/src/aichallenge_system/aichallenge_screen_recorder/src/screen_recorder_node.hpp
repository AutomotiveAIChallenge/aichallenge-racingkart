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

#include "encoder_worker.hpp"

#include <QObject>
#include <QString>
#include <QTimer>

#include <opencv2/opencv.hpp>
#include <rclcpp/rclcpp.hpp>

#include <std_srvs/srv/trigger.hpp>

#include <atomic>
#include <functional>
#include <memory>
#include <string>

class ScreenRecorder : public QObject
{
  Q_OBJECT

public:
  explicit ScreenRecorder(int hz, QObject * parent = nullptr);
  ~ScreenRecorder() override = default;

  // Thread-safe: the single source of truth for the recording state.
  bool active() const { return active_.load(); }

signals:
  void statusChanged(bool active, QString message);

public slots:
  bool start(const QString & path);  // returns whether recording is active afterwards
  void stop();

private slots:
  void onTick();

private:
  cv::Mat grabFrame();

  int hz_;
  cv::Size size_{};
  std::atomic<bool> active_{false};
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
  bool callOnQtThread(std::function<bool()> fn);

  ScreenRecorder * recorder_;
  std::string output_dir_;
  std::string prefix_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
};

#endif  // SCREEN_RECORDER_NODE_HPP_
