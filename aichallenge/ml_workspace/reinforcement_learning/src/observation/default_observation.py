from __future__ import annotations

import numpy as np
from gymnasium import spaces
import cv2

from context.context_types import StepContext
from observation.interfaces import ObservationBuilder

# ORIGINAL SIZE
# IMAGE_HEIGHT = 256
# IMAGE_WIDTH = 384

IMAGE_HEIGHT = 64
IMAGE_WIDTH = 64

IMAGE_CHANNELS = 3


class ImageSpeedObservationBuilder(ObservationBuilder):
    @property
    def observation_space(self) -> spaces.Dict:
        return spaces.Dict(
            {
                "image": spaces.Box(
                    low=0,
                    high=255,
                    shape=(IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANNELS),
                    dtype=np.uint8,
                ),
                "speed": spaces.Box(
                    low=0.0,
                    high=np.finfo(np.float32).max,
                    shape=(1,),
                    dtype=np.float32,
                ),
            }
        )

    def build(self, context: StepContext) -> tuple[dict[str, np.ndarray], StepContext]:
        image = context.env_state.get_value("camera_image")
        if image is None:
            image = np.zeros((IMAGE_HEIGHT, IMAGE_WIDTH, IMAGE_CHANNELS), dtype=np.uint8)
        else:
            image = cv2.resize(image, (IMAGE_WIDTH, IMAGE_HEIGHT))

        speed_value = float(context.env_state.get_value("vehicle_speed_mps", 0.0))
        speed = np.array([max(0.0, speed_value)], dtype=np.float32)

        observation = {
            "image": image,
            "speed": speed,
        }
        return observation, context
