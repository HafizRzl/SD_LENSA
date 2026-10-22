"""Train LENSA's road-hazard detector: potholes, broken road and manhole covers, in ONE model.

Runs in the separate CUDA training venv (the beta's own Python is CPU-only):

    D:\\lensa-venv-train\\Scripts\\python.exe desktop-beta/train_hazards.py
    D:\\lensa-venv-train\\Scripts\\python.exe desktop-beta/train_hazards.py --epochs 5   # quick smoke run
    D:\\lensa-venv-train\\Scripts\\python.exe desktop-beta/train_hazards.py --resume     # continue a stopped run

Before training: plug the charger in (the GPU throttles on battery) and close heavy apps — RAM
headroom is tight because C: is too full for the Windows page file to grow.

Starts from the COCO-pretrained yolo26n.pt (transfer learning) and writes the best weights to
desktop-beta/models/road_hazard_yolo26n.pt, which lensa_beta.py loads as its second detector.
One combined model instead of one per dataset: each extra model costs a full extra inference.

Datasets (assets/dataset/, gitignored):
- pothole/  — "pothole and speed breaker detect" v1, Roboflow, CC BY 4.0. Grayscale, partly
  90°-rotated (Roboflow pre-processing). Classes 0 broken road, 1 potholes.
- manhole/  — "manhole clean", Ultralytics Platform (walrus-crow), from manhole-clean.ndjson.
  Pinyin class codes, read off the images: jgps broken cover, jgwh intact cover, jgsd missing
  cover (open shaft), jgwg displaced/ajar cover. Its labels are shifted +2 once (below) so both
  datasets share one class list.
"""
import argparse
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASETS = HERE.parent / "assets" / "dataset"
POTHOLE = DATASETS / "pothole"
MANHOLE = next((DATASETS / "manhole").glob("manhole-clean-*"), None)
OUT = HERE / "models" / "road_hazard_yolo26n.pt"
RUN = "road_hazard_v2"            # v1 (pothole only, test mAP50 0.727) stays in runs/road_hazard
# Class names as LENSA's catalog spells them (yolo_class).
NAMES = ["broken_road", "pothole",                          # pothole set: 0, 1
         "manhole_broken", "manhole_intact", "manhole_open", "manhole_displaced"]  # manhole: 0-3 -> 2-5
MANHOLE_OFFSET = 2


def shift_manhole_labels():
    """Manhole labels use 0-3, which collide with the pothole set's 0-1. Shift them to 2-5
    once; a marker file makes this idempotent (re-convert from the .ndjson to undo)."""
    marker = MANHOLE / ".lensa_class_offset"
    if marker.exists():
        return
    for f in (MANHOLE / "labels").rglob("*.txt"):
        rows = [ln.split() for ln in f.read_text().splitlines() if ln.strip()]
        f.write_text("".join(f"{int(r[0]) + MANHOLE_OFFSET} {' '.join(r[1:])}\n" for r in rows))
    marker.write_text(str(MANHOLE_OFFSET))


def data_yaml() -> Path:
    """One data file over both datasets (Ultralytics accepts lists of image folders)."""
    y = HERE / "runs" / "road_hazard_data.yaml"
    y.parent.mkdir(parents=True, exist_ok=True)
    p, m = POTHOLE.as_posix(), MANHOLE.as_posix()
    y.write_text(
        f"train: [{p}/train/images, {m}/images/train]\n"
        f"val: [{p}/valid/images, {m}/images/val]\n"
        f"test: [{p}/test/images, {m}/images/test]\n"
        f"names: {NAMES}\n", encoding="utf-8")
    return y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=16)   # fits an 8 GB RTX 4060 at 640 px
    ap.add_argument("--resume", action="store_true",
                    help=f"continue a stopped run from runs/{RUN}/weights/last.pt")
    args = ap.parse_args()

    from ultralytics import YOLO
    shift_manhole_labels()
    if args.resume:   # same epochs/settings as the original run, picks up at the next epoch
        YOLO(str(HERE / "runs" / RUN / "weights" / "last.pt")).train(resume=True)
    else:
        YOLO(str(HERE / "models" / "yolo26n.pt")).train(
            data=str(data_yaml()), epochs=args.epochs, imgsz=640, batch=args.batch, device=0,
            patience=15,          # stop early once val mAP stops improving
            degrees=0.0,          # the pothole set is already rotated; don't add more
            cache=False,          # disk/RAM cache would write GBs (C: is nearly full)
            workers=0,            # each Windows worker reloads CUDA (~1.5 GB) and C: is too full
                                  # for the page file to grow -> load data in the main process
            project=str(HERE / "runs"), name=RUN, exist_ok=True,
        )
    finish(YOLO)


def finish(YOLO):
    shutil.copy(HERE / "runs" / RUN / "weights" / "best.pt", OUT)
    metrics = YOLO(str(OUT)).val(data=str(data_yaml()), split="test", device=0, plots=True,
                                 workers=0, project=str(HERE / "runs"), name=f"{RUN}_test",
                                 exist_ok=True)
    print(f"\n[train] saved {OUT}")
    print(f"[train] TEST set  mAP50={metrics.box.map50:.3f}  mAP50-95={metrics.box.map:.3f}")
    for j, i in enumerate(metrics.box.ap_class_index):   # ap50 is per class present in test
        print(f"[train]   {NAMES[i]:18} AP50={metrics.box.ap50[j]:.3f}")


if __name__ == "__main__":
    main()
