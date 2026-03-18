"""Extract from rosbag, clip labels, augment (horizontal flip), and split into train/val."""
import shutil
import numpy as np
from pathlib import Path

all_dir = Path("dataset/all")
seq_dirs = sorted([p for p in all_dir.iterdir() if p.is_dir()])
if not seq_dirs:
    raise RuntimeError(f"No sequence directories found in {all_dir}")

print(f"Found {len(seq_dirs)} sequences: {[p.name for p in seq_dirs]}")

# --- Aggregate all sequences ---
all_imgs = []
all_steers = []
all_accels = []

for src in seq_dirs:
    required = ["images.npy", "steers.npy", "accelerations.npy"]
    if not all((src / f).exists() for f in required):
        print(f"Skipping {src.name}: missing .npy files")
        continue

    steers = np.clip(np.load(src / "steers.npy"), -1.0, 1.0)
    accels = np.clip(np.load(src / "accelerations.npy"), -1.0, 1.0)
    imgs = np.load(src / "images.npy", mmap_mode="r")

    n = len(steers)
    if n != len(imgs) or n != len(accels):
        print(f"Skipping {src.name}: length mismatch (imgs={len(imgs)}, steers={len(steers)}, accels={len(accels)})")
        continue

    all_imgs.append(np.array(imgs))
    all_steers.append(steers)
    all_accels.append(accels)
    print(f"  Loaded {src.name}: {n} samples")

if not all_imgs:
    raise RuntimeError(f"No valid sequences found in {all_dir}")

imgs = np.concatenate(all_imgs, axis=0)
steers = np.concatenate(all_steers, axis=0)
accels = np.concatenate(all_accels, axis=0)
del all_imgs, all_steers, all_accels

n = len(steers)
split = int(n * 0.8)
print(f"Total: {n} samples, split: train={split}, val={n - split}")

# --- Output dirs ---
train_dir = Path("dataset/train/merged")
val_dir = Path("dataset/val/merged")

for d in [train_dir, val_dir]:
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)

# --- Train: original + horizontal flip augmentation ---
train_imgs_orig = imgs[:split]
train_steers_orig = steers[:split]
train_accels_orig = accels[:split]

# Horizontal flip: mirror image, negate steering
train_imgs_flip = train_imgs_orig[:, :, ::-1, :].copy()  # flip W axis
train_steers_flip = -train_steers_orig
train_accels_flip = train_accels_orig  # accel stays the same

train_imgs = np.concatenate([train_imgs_orig, train_imgs_flip], axis=0)
train_steers = np.concatenate([train_steers_orig, train_steers_flip], axis=0)
train_accels = np.concatenate([train_accels_orig, train_accels_flip], axis=0)

np.save(train_dir / "images.npy", train_imgs)
np.save(train_dir / "steers.npy", train_steers)
np.save(train_dir / "accelerations.npy", train_accels)
del train_imgs, train_imgs_orig, train_imgs_flip

# --- Val: no augmentation ---
np.save(val_dir / "images.npy", imgs[split:])
np.save(val_dir / "steers.npy", steers[split:])
np.save(val_dir / "accelerations.npy", accels[split:])
del imgs

print(f"Train: {len(train_steers)} (original {split} + {split} flipped), Val: {n - split}")
print(f"Steer range: [{steers.min():.4f}, {steers.max():.4f}]")
print(f"Accel range: [{accels.min():.4f}, {accels.max():.4f}]")
