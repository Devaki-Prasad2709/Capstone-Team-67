#!/usr/bin/env python
"""
Pipeline completion verification script.
Checks if each pipeline stage completed successfully and reports status/metrics.
"""

import json
from pathlib import Path
import csv


def check_stage(name: str, condition: bool, details: str = ""):
    """Print stage completion status."""
    status = "✓" if condition else "✗"
    msg = f"{status} {name}"
    if details:
        msg += f" - {details}"
    print(msg)
    return condition


def main():
    repo_root = Path(__file__).parent
    
    print("\n" + "="*70)
    print("PIPELINE VERIFICATION REPORT")
    print("="*70)
    
    all_good = True
    
    # Stage 1: ISBDA Processing
    print("\n[STAGE 1] ISBDA Dataset Processing")
    isbda_annotations = repo_root / "datasets/processed/drone/isbda/annotations/instances.json"
    isbda_images = repo_root / "datasets/processed/drone/isbda/images"
    isbda_labels = repo_root / "datasets/processed/drone/isbda/labels.jsonl"
    
    if isbda_annotations.exists():
        with isbda_annotations.open() as f:
            coco_data = json.load(f)
        n_images = len(coco_data.get("images", []))
        n_annotations = len(coco_data.get("annotations", []))
        n_categories = len(coco_data.get("categories", []))
        all_good &= check_stage(
            "COCO Annotations merged",
            True,
            f"{n_images} images, {n_annotations} annotations, {n_categories} categories"
        )
    else:
        all_good &= check_stage("COCO Annotations merged", False)
    
    n_isbda_imgs = len(list(isbda_images.glob("*.jpg"))) + len(list(isbda_images.glob("*.png"))) if isbda_images.exists() else 0
    all_good &= check_stage("Images copied to processed", isbda_images.exists(), f"{n_isbda_imgs} images")
    
    all_good &= check_stage("Labels JSONL created", isbda_labels.exists())
    
    # Stage 2: COCO→YOLO Conversion
    print("\n[STAGE 2] COCO to YOLO Conversion")
    yolo_images_all = repo_root / "processed/drone/images/all"
    yolo_labels_all = repo_root / "processed/drone/labels/all"
    classes_file = repo_root / "processed/drone/classes.txt"
    
    n_yolo_imgs = len(list(yolo_images_all.glob("*.jpg"))) + len(list(yolo_images_all.glob("*.png"))) if yolo_images_all.exists() else 0
    n_yolo_labels = len(list(yolo_labels_all.glob("*.txt"))) if yolo_labels_all.exists() else 0
    
    all_good &= check_stage("YOLO images in all/", yolo_images_all.exists(), f"{n_yolo_imgs} images")
    all_good &= check_stage("YOLO labels in all/", yolo_labels_all.exists(), f"{n_yolo_labels} labels")
    
    if classes_file.exists():
        with classes_file.open() as f:
            classes = [line.strip() for line in f if line.strip()]
        all_good &= check_stage("Classes defined", True, f"{len(classes)} classes")
    else:
        all_good &= check_stage("Classes defined", False)
    
    # Stage 3: Train/Val Split
    print("\n[STAGE 3] Train/Val Dataset Split")
    yolo_images_train = repo_root / "processed/drone/images/train"
    yolo_labels_train = repo_root / "processed/drone/labels/train"
    yolo_images_val = repo_root / "processed/drone/images/val"
    yolo_labels_val = repo_root / "processed/drone/labels/val"
    
    n_train_imgs = len(list(yolo_images_train.glob("*.jpg"))) + len(list(yolo_images_train.glob("*.png"))) if yolo_images_train.exists() else 0
    n_train_labels = len(list(yolo_labels_train.glob("*.txt"))) if yolo_labels_train.exists() else 0
    n_val_imgs = len(list(yolo_images_val.glob("*.jpg"))) + len(list(yolo_images_val.glob("*.png"))) if yolo_images_val.exists() else 0
    n_val_labels = len(list(yolo_labels_val.glob("*.txt"))) if yolo_labels_val.exists() else 0
    
    all_good &= check_stage("Training images split", yolo_images_train.exists(), f"{n_train_imgs} images")
    all_good &= check_stage("Training labels split", yolo_labels_train.exists(), f"{n_train_labels} labels")
    all_good &= check_stage("Validation images split", yolo_images_val.exists(), f"{n_val_imgs} images")
    all_good &= check_stage("Validation labels split", yolo_labels_val.exists(), f"{n_val_labels} labels")
    
    # Stage 4: data.yaml
    print("\n[STAGE 4] YOLOv8 Configuration")
    data_yaml = repo_root / "processed/drone/data.yaml"
    all_good &= check_stage("data.yaml created", data_yaml.exists())
    
    # Stage 5: Training Results
    print("\n[STAGE 5] YOLOv8 Model Training")
    runs_dir = repo_root / "runs/detect/train"
    weights_dir = runs_dir / "weights" if runs_dir.exists() else None
    best_weights = weights_dir / "best.pt" if weights_dir else None
    results_csv = runs_dir / "results.csv" if runs_dir.exists() else None
    
    training_complete = runs_dir.exists() and best_weights and best_weights.exists()
    all_good &= check_stage("Model training complete", training_complete)
    
    if results_csv and results_csv.exists():
        with results_csv.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if rows:
                last_row = rows[-1]
                metrics = f"Epoch {len(rows)}: mAP50={last_row.get('metrics/mAP50(B)', 'N/A')}"
                check_stage("Training metrics recorded", True, metrics)
    
    # Summary
    print("\n" + "="*70)
    if all_good:
        print("✓ PIPELINE EXECUTION SUCCESSFUL")
        print(f"\nDataset Summary:")
        print(f"  - Total images (all/): {n_yolo_imgs}")
        print(f"  - Training set: {n_train_imgs} images, {n_train_labels} labels")
        print(f"  - Validation set: {n_val_imgs} images, {n_val_labels} labels")
        print(f"  - Classes: {len(classes) if 'classes' in locals() else 'N/A'}")
        
        if training_complete:
            print(f"\nTraining Output:")
            print(f"  - Location: {runs_dir}")
            print(f"  - Best model: {best_weights}")
            print(f"  - Logs: {results_csv}")
    else:
        print("✗ PIPELINE INCOMPLETE - Some stages failed or are missing")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
