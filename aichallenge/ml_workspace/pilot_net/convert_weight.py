import argparse
from pathlib import Path
from typing import Dict
import numpy as np
import torch

from lib.model import PilotNet


def extract_params_to_dict(model: torch.nn.Module) -> Dict[str, np.ndarray]:
    """Extracts the state dictionary from a PyTorch model and converts it to a NumPy dictionary.

    This function acts as a pure transformation layer, isolating the logic of
    parameter extraction and naming convention changes (dot to underscore) from
    file I/O operations. This design ensures high testability.

    Args:
        model: The PyTorch model instance to extract weights from.

    Returns:
        A dictionary mapping parameter names (with underscores replaced) to
        detached NumPy arrays on the CPU.
    """
    return {
        k.replace('.', '_'): v.detach().cpu().numpy()
        for k, v in model.state_dict().items()
    }


def save_numpy_dict(params: Dict[str, np.ndarray], output_path: Path) -> None:
    """Saves a NumPy dictionary to a file system path.

    Handles the creation of parent directories if they do not exist and
    persists the parameter dictionary as a .npy file.

    Args:
        params: The dictionary of model parameters.
        output_path: The filesystem path where the .npy file will be written.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(output_path, params)
    print(f"Saved NumPy weights to: {output_path}")


def load_model(
    image_height: int, image_width: int, output_dim: int, ckpt_path: Path
) -> torch.nn.Module:
    """Initializes the model architecture and loads weights from a checkpoint.

    Args:
        image_height: The input image height.
        image_width: The input image width.
        output_dim: The size of the output dimension (e.g., control commands).
        ckpt_path: The path to the PyTorch checkpoint file (.pth).

    Returns:
        The PyTorch model instance with loaded weights.

    Raises:
        FileNotFoundError: If the checkpoint file does not exist at ckpt_path.
    """
    model = PilotNet(image_height=image_height, image_width=image_width, output_dim=output_dim)
    state_dict = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    model.load_state_dict(state_dict)
    print(f"Loaded checkpoint: {ckpt_path}")
    return model


def convert_checkpoint(
    image_height: int, image_width: int, output_dim: int, ckpt: Path, output: Path
) -> None:
    """Orchestrates the model conversion process.

    This function combines the loading of the model architecture, the extraction
    of parameters into a pure dictionary format, and the saving to disk.

    Args:
        image_height: The input image height.
        image_width: The input image width.
        output_dim: The output dimension size.
        ckpt: The source path to the PyTorch checkpoint.
        output: The destination path for the converted NumPy file.
    """
    # 1. Load Model (I/O & Logic)
    model = load_model(image_height, image_width, output_dim, ckpt)

    # 2. Extract Parameters (Pure Logic) -> Easy to Unit Test
    params = extract_params_to_dict(model)

    # 3. Save to Disk (I/O)
    save_numpy_dict(params, output)


def main() -> None:
    """Main entry point for the command-line interface.

    Parses command-line arguments and triggers the checkpoint conversion.
    """
    parser = argparse.ArgumentParser(
        description="Convert PilotNet PyTorch weights to NumPy.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--image-height", type=int, default=256, help="Input image height")
    parser.add_argument("--image-width", type=int, default=384, help="Input image width")
    parser.add_argument("--output-dim", type=int, default=2, help="Output dimension size")
    parser.add_argument("--ckpt", type=Path, required=True, help="Source .pth checkpoint")
    parser.add_argument("--output", type=Path, default=Path("./weights/pilotnet_weights.npy"), help="Destination .npy path")

    args = parser.parse_args()

    convert_checkpoint(args.image_height, args.image_width, args.output_dim, args.ckpt, args.output)


if __name__ == "__main__":
    main()
