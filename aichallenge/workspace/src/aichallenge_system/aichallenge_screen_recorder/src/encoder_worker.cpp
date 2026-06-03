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

#include "encoder_worker.hpp"

#include <cstdio>
#include <memory>
#include <utility>

EncoderWorker::EncoderWorker(
  std::unique_ptr<cv::VideoWriter> writer, cv::Size size, std::size_t max_queue)
: writer_(std::move(writer)), size_(size), max_queue_(max_queue)
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
      if (dropped_ == 1 || dropped_ % 100 == 0) {
        std::fprintf(
          stderr, "[screen_recorder] encoder queue full, dropped %zu frames so far\n", dropped_);
      }
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
  if (writer_) {
    writer_->release();
    writer_.reset();
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
    if (frame.empty() || !writer_) {
      continue;
    }
    if (frame.cols != size_.width || frame.rows != size_.height) {
      cv::Mat resized;
      cv::resize(frame, resized, size_);
      writer_->write(resized);
    } else {
      writer_->write(frame);
    }
  }
}
