from pathlib import Path
import torch
import torch.optim as optim
from torch.utils.data import DataLoader
from tqdm import tqdm
import hydra
from omegaconf import DictConfig, OmegaConf
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime
import yaml

from lib.model import TinyLidarNet, TinyLidarNetSmall
from lib.data import MultiSeqConcatDataset
from lib.loss import WeightedSmoothL1Loss


def load_common_params(path):
    """Load training parameters from the ROS parameter file used by inference."""
    params = {"input_dim": 750, "max_range": 30.0, "accel_scale": 1.0, "decel_scale": 1.0}
    if path is None:  # Older train.yaml files did not specify a common file.
        return params

    param_path = Path(path).expanduser()
    if not param_path.is_absolute():
        param_path = Path(__file__).resolve().parent / param_path
    with param_path.open() as f:
        ros_params = (yaml.safe_load(f) or {}).get("/**", {}).get("ros__parameters", {})
    if "input_dim" in ros_params.get("model", {}):
        params["input_dim"] = int(ros_params["model"]["input_dim"])
    for key in ("max_range", "accel_scale", "decel_scale"):
        if key in ros_params:
            params[key] = float(ros_params[key])
    if params["accel_scale"] <= 0.0 or params["decel_scale"] <= 0.0:
        raise ValueError("accel_scale and decel_scale must be positive")
    return params


def clean_numerical_tensor(x: torch.Tensor) -> torch.Tensor:
    """NaN, infを安全に除去"""
    if torch.isnan(x).any() or torch.isinf(x).any():
        x = torch.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    return x


@hydra.main(config_path="./config", config_name="train", version_base="1.2")
def main(cfg: DictConfig):
    print("------ Configuration ------")
    print(OmegaConf.to_yaml(cfg))
    print("---------------------------")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # === Common parameters (shared with the inference node) ===
    # 古い train.yaml（common_param_path が無い）では従来の既定値を使う
    common = load_common_params(cfg.get("common_param_path"))
    input_dim = cfg.model.get("input_dim", common["input_dim"])
    if input_dim != common["input_dim"]:
        raise ValueError(
            f"model.input_dim ({input_dim}) differs from input_dim in the common parameter file "
            f"({common['input_dim']}). Set it only in the common parameter file."
        )
    print(f"Common parameters: {common}")

    # === Dataset ===
    dataset_kwargs = dict(
        max_range=common["max_range"],
        accel_scale=common["accel_scale"],
        decel_scale=common["decel_scale"],
    )
    train_dataset = MultiSeqConcatDataset(cfg.data.train_dir, **dataset_kwargs)
    val_dataset = MultiSeqConcatDataset(cfg.data.val_dir, **dataset_kwargs)

    if len(train_dataset) == 0:
        raise RuntimeError(
            f"Training dataset is empty (train_dir={cfg.data.train_dir}). "
            "Cannot train without data."
        )
    if len(val_dataset) == 0:
        print("[WARN] Validation dataset is empty. Validation loss will be reported as inf.")

    effective_batch_size = cfg.train.batch_size
    drop_last = True
    if len(train_dataset) < cfg.train.batch_size:
        effective_batch_size = len(train_dataset)
        drop_last = False
        print(
            f"[WARN] train_dataset size ({len(train_dataset)}) < batch_size ({cfg.train.batch_size}). "
            f"Using batch_size={effective_batch_size} with drop_last=False."
        )

    train_loader = DataLoader(
        train_dataset,
        batch_size=effective_batch_size,
        shuffle=True,
        num_workers=cfg.train.num_workers,
        pin_memory=True,
        drop_last=drop_last
    )

    val_loader = DataLoader(
        val_dataset,
        batch_size=cfg.train.batch_size,
        shuffle=False,
        num_workers=cfg.train.num_workers,
        pin_memory=True,
        drop_last=False
    )

    # === Model ===
    if cfg.model.name == "TinyLidarNetSmall":
        model = TinyLidarNetSmall(
            input_dim=input_dim,
            output_dim=cfg.model.output_dim
        ).to(device)
    else:
        model = TinyLidarNet(
            input_dim=input_dim,
            output_dim=cfg.model.output_dim
        ).to(device)

    if cfg.train.pretrained_path:
        model.load_state_dict(torch.load(cfg.train.pretrained_path))
        print(f"[INFO] Loaded pretrained model from {cfg.train.pretrained_path}")

    # === Loss & Optimizer ===
    criterion = WeightedSmoothL1Loss(
        steer_weight=cfg.train.loss.steer_weight,
        accel_weight=cfg.train.loss.accel_weight
    )
    optimizer = optim.Adam(model.parameters(), lr=cfg.train.lr)

    # === Logging & Save dirs ===
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_dir = Path(cfg.train.save_dir).expanduser().resolve()
    log_dir = Path(cfg.train.log_dir).expanduser().resolve()
    save_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    with SummaryWriter(log_dir / timestamp) as writer:
        best_val_loss = float("inf")
        patience_counter = 0
        max_patience = cfg.train.get("early_stop_patience", 10)

        best_path = save_dir / "best_model.pth"
        last_path = save_dir / "last_model.pth"

        # === Training Loop ===
        for epoch in range(cfg.train.epochs):
            model.train()
            train_loss = 0.0

            for scans, targets in tqdm(train_loader, desc=f"[Train] Epoch {epoch+1}/{cfg.train.epochs}"):
                scans = scans.unsqueeze(1).to(device)
                targets = targets.to(device)

                scans = clean_numerical_tensor(scans)
                targets = clean_numerical_tensor(targets)

                outputs = model(scans)
                loss = criterion(outputs, targets)

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            avg_train_loss = train_loss / len(train_loader)
            avg_val_loss = validate(model, val_loader, device, criterion)

            print(f"Epoch {epoch+1:03d}: Train={avg_train_loss:.4f} | Val={avg_val_loss:.4f}")
            writer.add_scalar("Loss/train", avg_train_loss, epoch + 1)
            writer.add_scalar("Loss/val", avg_val_loss, epoch + 1)

            if avg_val_loss < best_val_loss:
                best_val_loss = avg_val_loss
                torch.save(model.state_dict(), best_path)
                print(f"[SAVE] Best model updated: {best_path} (val_loss={best_val_loss:.4f})")
                patience_counter = 0
            else:
                patience_counter += 1

            torch.save(model.state_dict(), last_path)
            if patience_counter >= max_patience:
                print(f"[EarlyStop] No improvement for {max_patience} epochs.")
                break
    
    print("Training finished.")


def validate(model, loader, device, criterion):
    model.eval()
    total_loss = 0.0
    n_batches = 0
    with torch.no_grad():
        for scans, targets in tqdm(loader, desc="[Val]", leave=False):
            scans = scans.unsqueeze(1).to(device)
            targets = targets.to(device)
            scans = clean_numerical_tensor(scans)
            targets = clean_numerical_tensor(targets)
            outputs = model(scans)
            loss = criterion(outputs, targets)
            total_loss += loss.item()
            n_batches += 1
    return total_loss / n_batches if n_batches > 0 else float("inf")


if __name__ == "__main__":
    main()
