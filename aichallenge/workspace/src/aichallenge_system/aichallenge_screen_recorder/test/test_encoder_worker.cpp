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

#include <gtest/gtest.h>

#include <atomic>
#include <cstddef>
#include <future>
#include <memory>
#include <utility>

namespace
{
// VideoWriter mock: blocks inside the first write() until the gate opens, so the test can fill
// the queue deterministically while the worker is busy.
class GateWriter : public cv::VideoWriter
{
public:
  GateWriter(std::atomic<int> & written, std::promise<void> & first_write, std::shared_future<void> gate)
  : written_(written), first_write_(first_write), gate_(std::move(gate))
  {
  }

  bool isOpened() const override { return true; }
  void release() override {}
  void write(cv::InputArray /*image*/) override
  {
    if (!started_) {
      started_ = true;
      first_write_.set_value();
      gate_.wait();
    }
    ++written_;
  }

private:
  std::atomic<int> & written_;
  std::promise<void> & first_write_;
  std::shared_future<void> gate_;
  bool started_{false};
};
}  // namespace

TEST(EncoderWorker, DropsFramesWhenQueueIsFullAndDrainsOnStop)
{
  std::atomic<int> written{0};
  std::promise<void> first_write;
  auto first_write_started = first_write.get_future();
  std::promise<void> gate;
  auto writer = std::make_unique<GateWriter>(written, first_write, gate.get_future().share());

  const cv::Size size(4, 4);
  constexpr std::size_t kMaxQueue = 3;
  EncoderWorker worker(std::move(writer), size, kMaxQueue);

  const cv::Mat frame(size, CV_8UC3, cv::Scalar(0, 0, 0));

  // The worker pops this frame and blocks inside write() until the gate opens.
  worker.submit(frame.clone());
  first_write_started.wait();

  // Queue is empty and the worker is blocked: fill the queue, then overflow it.
  for (std::size_t i = 0; i < kMaxQueue; ++i) {
    worker.submit(frame.clone());
  }
  worker.submit(frame.clone());  // dropped
  worker.submit(frame.clone());  // dropped

  gate.set_value();
  const std::size_t dropped = worker.stop();

  EXPECT_EQ(dropped, 2u);
  EXPECT_EQ(written.load(), static_cast<int>(1 + kMaxQueue));
}
