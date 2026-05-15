import logging
import numpy as np
import cv2
from typing import Tuple

from model.pilotnet import PilotNetNp


class PilotNetCore:
    """Core logic for the PilotNet autonomous driving controller.

    Manages the neural network model lifecycle, image preprocessing,
    weight loading, and inference execution.

    Attributes:
        image_height (int): Target image height for the model.
        image_width (int): Target image width for the model.
        output_dim (int): Dimension of output (1=steer only, 2=accel+steer).
        acceleration (float): Fixed acceleration for 'fixed' control mode or output_dim=1.
        control_mode (str): 'ai' or 'fixed'.
        model (PilotNetNp): The neural network model.
    """

    def __init__(
        self,
        image_height: int = 66,
        image_width: int = 200,
        output_dim: int = 2,
        ckpt_path: str = '',
        acceleration: float = 0.1,
        control_mode: str = 'ai',
        color_space: str = 'rgb',
        crop_top_ratio: float = 0.0,
        crop_bottom_ratio: float = 0.0,
    ):
        if crop_top_ratio + crop_bottom_ratio >= 1.0:
            raise ValueError(f"crop_top_ratio + crop_bottom_ratio must be < 1.0, got {crop_top_ratio} + {crop_bottom_ratio}")
        if color_space.lower() not in ("rgb", "yuv"):
            raise ValueError(f"Unsupported color_space: {color_space!r}, must be 'rgb' or 'yuv'")
        self.image_height = image_height
        self.image_width = image_width
        self.output_dim = output_dim
        self.acceleration = acceleration
        self.control_mode = control_mode.lower()
        self.color_space = color_space.lower()
        self.crop_top_ratio = crop_top_ratio
        self.crop_bottom_ratio = crop_bottom_ratio
        self.logger = logging.getLogger(__name__)

        self.model = PilotNetNp(
            image_height=self.image_height,
            image_width=self.image_width,
            output_dim=self.output_dim,
        )

        if ckpt_path:
            self._load_weights(ckpt_path)
        else:
            self.logger.warning("No weight file provided. Using randomly initialized weights.")

    def process(self, image: np.ndarray) -> Tuple[float, float]:
        """Runs inference on a camera image.

        Args:
            image (np.ndarray): RGB image array of shape (H, W, 3), dtype uint8.

        Returns:
            Tuple[float, float]: (acceleration, steering_angle), clipped to [-1, 1].
        """
        # 1. Preprocess image
        processed = self._preprocess_image(image)

        # 2. Prepare input: (1, 3, H, W)
        x = processed.transpose(2, 0, 1)  # HWC → CHW
        x = np.expand_dims(x, axis=0)  # add batch dim

        # 3. Inference
        outputs = self.model(x)[0]

        # 4. Post-process
        if self.output_dim == 1:
            accel = self.acceleration
            steer = float(np.clip(outputs[0], -1.0, 1.0))
        elif self.control_mode == "ai":
            accel = float(np.clip(outputs[0], -1.0, 1.0))
            steer = float(np.clip(outputs[1], -1.0, 1.0))
        else:
            accel = self.acceleration
            steer = float(np.clip(outputs[1], -1.0, 1.0))

        return accel, steer

    def _load_weights(self, path: str) -> None:
        """Loads model weights from a .npy or .npz file.
        EXACTLY the same pattern as TinyLidarNetCore._load_weights.
        """
        try:
            weights = np.load(path, allow_pickle=True)

            if isinstance(weights, np.lib.npyio.NpzFile):
                weight_dict = dict(weights.items())
            elif isinstance(weights, np.ndarray) and weights.dtype == object:
                weight_dict = weights.item()
            elif isinstance(weights, dict):
                weight_dict = weights
            else:
                raise ValueError(f"Unsupported weight format type: {type(weights)}")

            loaded_count = 0
            skipped_keys = []
            for key, value in weight_dict.items():
                key_norm = key.replace('.', '_')
                if key_norm in self.model.params:
                    self.model.params[key_norm] = value
                    loaded_count += 1
                else:
                    skipped_keys.append(key_norm)

            if skipped_keys:
                self.logger.warning(f"Skipped {len(skipped_keys)} unrecognized keys: {skipped_keys[:5]}")

            expected = len(self.model.params)
            if loaded_count < expected:
                self.logger.warning(f"Partial load: {loaded_count}/{expected} parameters loaded from {path}")
            else:
                self.logger.info(f"Successfully loaded {loaded_count} parameters from {path}")

        except Exception as e:
            self.logger.error(f"Failed to load weights from {path}: {e}")
            raise

    def _preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """Resizes and normalizes a camera image.

        1. Crop top/bottom (original paper removes sky and car body)
        2. Resize to (image_height, image_width)
        3. Convert color space if needed
        4. Normalize to [0, 1]

        Args:
            image (np.ndarray): Input image (H, W, 3) uint8.

        Returns:
            np.ndarray: Processed image (image_height, image_width, 3) float32 in [0, 1].
        """
        # Crop
        if self.crop_top_ratio > 0 or self.crop_bottom_ratio > 0:
            h = image.shape[0]
            top = int(h * self.crop_top_ratio)
            bottom = h - int(h * self.crop_bottom_ratio)
            image = image[top:bottom, :, :]

        # Resize
        resized = cv2.resize(image, (self.image_width, self.image_height), interpolation=cv2.INTER_LINEAR)

        # Color space conversion (original PilotNet paper uses YUV)
        if self.color_space == "yuv":
            resized = cv2.cvtColor(resized, cv2.COLOR_RGB2YUV)

        # Normalize to [0, 1] in single pass
        return np.multiply(resized, 1.0 / 255.0, dtype=np.float32)
