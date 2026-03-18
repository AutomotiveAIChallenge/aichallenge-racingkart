from pathlib import Path
import torch
import torch.optim as optim
from torch.utils.data import DataLoader
from tqdm import tqdm
import hydra
from omegaconf import DictConfig, OmegaConf
from torch.utils.tensorboard import SummaryWriter
from datetime import datetime

from lib.model import PilotNet
from lib.data import MultiSeqConcatDataset
from lib.loss import WeightedSmoothL1Loss


@hydra.main(config_path="./config", config_name="train", version_base="1.2")
def main(cfg: DictConfig):
    print("------ Configuration ------")
    print(OmegaConf.to_yaml(cfg))
    print("---------------------------")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # === Dataset ===
    train_dataset = MultiSeqConcatDataset(
        cfg.data.train_dir,
        image_height=cfg.model.image_height,
        image_width=cfg.model.image_width,
        training=True
    )
    val_dataset = MultiSeqConcatDataset(
        cfg.data.val_dir,
        image_height=cfg.model.image_height,
        image_width=cfg.model.image_width
    )

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
        drop_last=drop_last,
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
    model = PilotNet(
        image_height=cfg.model.image_height,
        image_width=cfg.model.image_width,
        output_dim=cfg.model.output_dim
    ).to(device)

    if cfg.train.pretrained_path:
        model.load_state_dict(torch.load(cfg.train.pretrained_path, weights_only=True))
        print(f"[INFO] Loaded pretrained model from {cfg.train.pretrained_path}")

    # === Loss & Optimizer ===
    loss_type = cfg.train.get("loss_type", "smooth_l1")
    if loss_type == "mse":
        criterion = torch.nn.MSELoss()
        print(f"[INFO] Using MSELoss")
    else:
        criterion = WeightedSmoothL1Loss(
            steer_weight=cfg.train.loss.steer_weight,
            accel_weight=cfg.train.loss.accel_weight
        )
        print(f"[INFO] Using WeightedSmoothL1Loss")
    optimizer = optim.Adam(model.parameters(), lr=cfg.train.lr, weight_decay=cfg.train.weight_decay)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.5, patience=5, verbose=True)

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

            for images, targets in tqdm(train_loader, desc=f"[Train] Epoch {epoch+1}/{cfg.train.epochs}"):
                images = images.to(device)
                targets = targets.to(device)

                outputs = model(images)
                loss = criterion(outputs, targets)

                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                optimizer.step()
                train_loss += loss.item()

            avg_train_loss = train_loss / len(train_loader)
            avg_val_loss = validate(model, val_loader, device, criterion)

            print(f"Epoch {epoch+1:03d}: Train={avg_train_loss:.4f} | Val={avg_val_loss:.4f}")
            writer.add_scalar("Loss/train", avg_train_loss, epoch + 1)
            writer.add_scalar("Loss/val", avg_val_loss, epoch + 1)

            scheduler.step(avg_val_loss)

            if avg_val_loss <= best_val_loss:
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
        for images, targets in tqdm(loader, desc="[Val]", leave=False):
            images = images.to(device)
            targets = targets.to(device)
            outputs = model(images)
            loss = criterion(outputs, targets)
            total_loss += loss.item()
            n_batches += 1
    if n_batches == 0:
        return float("inf")
    return total_loss / n_batches


if __name__ == "__main__":
    main()
