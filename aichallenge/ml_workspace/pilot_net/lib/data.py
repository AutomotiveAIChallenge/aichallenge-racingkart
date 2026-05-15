import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np
import cv2
from torch.utils.data import Dataset, ConcatDataset

logger = logging.getLogger(__name__)


class ImageControlSequenceDataset(Dataset):
    """Dataset for a single sequence of camera images and control commands.

    Loads images.npy (or individual image files) and control .npy files.
    Images are resized and normalized.

    Attributes:
        seq_dir (Path): Path to the sequence directory.
        image_height (int): Target image height.
        image_width (int): Target image width.
        images: Image data.
        steers: Steering angles.
        accels: Accelerations.
    """

    def __init__(
        self,
        seq_dir: Union[str, Path],
        image_height: int = 66,
        image_width: int = 200,
        training: bool = False,
        color_space: str = "rgb",
        crop_top_ratio: float = 0.0,
        crop_bottom_ratio: float = 0.0,
        output_dim: int = 2,
        shift_range: float = 0.0,
        steer_correction_per_pixel: float = 0.004,
    ):
        if crop_top_ratio + crop_bottom_ratio >= 1.0:
            raise ValueError(f"crop_top_ratio + crop_bottom_ratio must be < 1.0, got {crop_top_ratio} + {crop_bottom_ratio}")
        if color_space.lower() not in ("rgb", "yuv"):
            raise ValueError(f"Unsupported color_space: {color_space!r}, must be 'rgb' or 'yuv'")
        self.seq_dir = Path(seq_dir)
        self.image_height = image_height
        self.image_width = image_width
        self.training = training
        self.color_space = color_space.lower()
        self.crop_top_ratio = crop_top_ratio
        self.crop_bottom_ratio = crop_bottom_ratio
        self.output_dim = output_dim
        self.shift_range = shift_range
        self.steer_correction_per_pixel = steer_correction_per_pixel

        try:
            self.images = np.load(self.seq_dir / "images.npy", mmap_mode='r')  # (N, H, W, 3) uint8
            self.steers = np.load(self.seq_dir / "steers.npy")
            self.accels = np.load(self.seq_dir / "accelerations.npy")
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Missing required .npy files in {self.seq_dir}: {e}")

        n_samples = len(self.images)
        if not (len(self.steers) == n_samples and len(self.accels) == n_samples):
            raise ValueError(
                f"Data length mismatch in {self.seq_dir}: "
                f"Images={len(self.images)}, Steers={len(self.steers)}, Accels={len(self.accels)}"
            )

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        """Returns (image, target) pair.

        image: (3, image_height, image_width) float32 normalized [0,1]
        target: [steer] (output_dim=1) or [accel, steer] (output_dim=2) float32
        """
        img = self.images[idx]  # (H, W, 3) uint8
        steer = np.float32(self.steers[idx])

        # 1. Crop (original paper removes sky and car body)
        if self.crop_top_ratio > 0 or self.crop_bottom_ratio > 0:
            h = img.shape[0]
            top = int(h * self.crop_top_ratio)
            bottom = h - int(h * self.crop_bottom_ratio)
            img = img[top:bottom, :, :]

        # 2. Resize
        if img.shape[0] != self.image_height or img.shape[1] != self.image_width:
            img = cv2.resize(img, (self.image_width, self.image_height), interpolation=cv2.INTER_LINEAR)

        # 3. Geometric augmentation (on uint8, before color conversion)
        if self.training and self.shift_range > 0:
            img, steer = self._geometric_augment(img, steer)

        # 4. Color space conversion (original PilotNet paper uses YUV)
        if self.color_space == "yuv":
            img = cv2.cvtColor(img, cv2.COLOR_RGB2YUV)

        # 5. Normalize to [0, 1]
        img = img.astype(np.float32) / 255.0

        # 6. Photometric augmentation (on float [0,1])
        if self.training:
            img = self._photometric_augment(img)

        # Transpose: HWC -> CHW
        img = img.transpose(2, 0, 1)  # (3, H, W)

        if self.output_dim == 1:
            target = np.array([steer], dtype=np.float32)
        else:
            accel = np.float32(self.accels[idx])
            target = np.array([accel, steer], dtype=np.float32)

        return img, target

    def _geometric_augment(self, img: np.ndarray, steer: float) -> Tuple[np.ndarray, float]:
        """Apply random horizontal shift and adjust steering angle accordingly.

        This replicates the original PilotNet paper's augmentation strategy
        to teach the network recovery behavior.
        """
        if np.random.random() < 0.5:
            shift = np.random.uniform(-self.shift_range, self.shift_range)
            M = np.float32([[1, 0, shift], [0, 1, 0]])
            img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]))
            steer = np.float32(steer + shift * self.steer_correction_per_pixel)
        return img, steer

    @staticmethod
    def _photometric_augment(img: np.ndarray) -> np.ndarray:
        """Apply random photometric augmentations to a [0,1] float32 image."""
        # Brightness: +/- 20%
        if np.random.random() < 0.5:
            delta = np.random.uniform(-0.2, 0.2)
            img = img + delta

        # Contrast: 0.8 - 1.2x
        if np.random.random() < 0.5:
            factor = np.random.uniform(0.8, 1.2)
            img = (img - 0.5) * factor + 0.5

        # Gaussian noise
        if np.random.random() < 0.3:
            noise = np.random.normal(0, 0.02, img.shape).astype(np.float32)
            img = img + noise

        # Gaussian blur
        if np.random.random() < 0.2:
            img = cv2.GaussianBlur(img, (3, 3), 0)

        np.clip(img, 0.0, 1.0, out=img)
        return img


class MultiSeqConcatDataset(Dataset):
    """Aggregates multiple ImageControlSequenceDatasets.
    Same pattern as tiny_lidar_net's MultiSeqConcatDataset.
    Wraps ConcatDataset but supports the empty-dataset case.
    """

    def __init__(
        self,
        dataset_root: Union[str, Path],
        image_height: int = 66,
        image_width: int = 200,
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        training: bool = False,
        color_space: str = "rgb",
        crop_top_ratio: float = 0.0,
        crop_bottom_ratio: float = 0.0,
        output_dim: int = 2,
        shift_range: float = 0.0,
        steer_correction_per_pixel: float = 0.004,
    ):
        dataset_root = Path(dataset_root)
        all_seq_dirs = sorted([p for p in dataset_root.iterdir() if p.is_dir()])
        target_seq_dirs = []

        for p in all_seq_dirs:
            name = p.name
            if include and not any(inc in name for inc in include):
                continue
            if exclude and any(exc in name for exc in exclude):
                continue
            target_seq_dirs.append(p)

        datasets = []
        for seq_dir in target_seq_dirs:
            required_files = ["images.npy", "steers.npy", "accelerations.npy"]
            if all((seq_dir / f).exists() for f in required_files):
                try:
                    ds = ImageControlSequenceDataset(
                        seq_dir,
                        image_height=image_height,
                        image_width=image_width,
                        training=training,
                        color_space=color_space,
                        crop_top_ratio=crop_top_ratio,
                        crop_bottom_ratio=crop_bottom_ratio,
                        output_dim=output_dim,
                        shift_range=shift_range,
                        steer_correction_per_pixel=steer_correction_per_pixel,
                    )
                    datasets.append(ds)
                except Exception as e:
                    logger.warning(f"Failed to load sequence {seq_dir}: {e}")
            else:
                logger.warning(f"Skipping {seq_dir.name}: Missing .npy files.")

        if not datasets:
            logger.warning(f"No valid sequences found in {dataset_root}.")
            self._inner = None
            self._length = 0
            return

        self._inner = ConcatDataset(datasets)
        self._length = len(self._inner)
        logger.info(f"Loaded {len(datasets)} sequences from {dataset_root}. Total samples: {self._length}")

    def __len__(self):
        return self._length

    def __getitem__(self, idx):
        if self._inner is None:
            raise IndexError("Dataset is empty")
        return self._inner[idx]
