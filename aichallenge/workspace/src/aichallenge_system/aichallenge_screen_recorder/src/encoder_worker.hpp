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

#ifndef ENCODER_WORKER_HPP_
#define ENCODER_WORKER_HPP_

#include <opencv2/opencv.hpp>

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <memory>
#include <mutex>
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

#endif  // ENCODER_WORKER_HPP_
