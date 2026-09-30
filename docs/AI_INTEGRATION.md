# AI integration and model lineage

The active computer-vision worker serves the tracked RescueNet YOLO26s
segmentation checkpoint at
`ai/computer_vision/artifacts/drone_detector/weights/best.pt`.

## Active model

- Model event name: `drone_detector_yolo26s_seg`
- Architecture/task: YOLO26s-seg / instance segmentation
- Checkpoint SHA-256: `2d687e94fa5c2ef445c0888794de99ea1155d880ddb9be8f9237617eb55068b5`
- Classes: water, building damage (none/minor/major/total), vehicle, road
  (clear/blocked), tree, and pool
- Runtime output: class, confidence, bounding box, and a polygon for
  damage-relevant classes

The tracked `last.pt`, `results.csv`, box/mask curves, confusion matrices,
batch previews, and validation previews come from the same
`rescuenet_yolo26s_seg_final` training run. Their registered hashes are in
`ai/computer_vision/artifact_manifest.json`.

## Embedded training metrics

| Output | Precision | Recall | mAP@50 | mAP@50-95 |
| --- | ---: | ---: | ---: | ---: |
| Boxes | 0.74283 | 0.66660 | 0.72572 | 0.52265 |
| Masks | 0.73554 | 0.66361 | 0.71121 | 0.46847 |

`python -m scripts.verify_ai_artifacts` checks all registered hashes and
compares both box and mask metrics embedded in `best.pt` with the manifest.
The independent held-out test and polygon checks are recorded in
`docs/SEGMENTATION_VERIFICATION.md`.

## Runtime route

```text
drone-video -> AI worker -> ai-analysis-results -> Spark -> storage/processed/ai
     |              |
     +-> MinIO -----+
```

Results have one of four statuses: `analyzed`, `filtered`,
`duplicate_skipped`, or `error`.

For scenario events, every live detection retains its image/object reference,
class, confidence, bounding box, optional segmentation polygon, scenario
timestamp, explicitly simulated GPS, and declared GIS target. The result also
records the checkpoint SHA-256, `live-checkpoint-inference`, and
`prerecorded_output=false`. The observation consumer accepts simulated
coordinates only with `--allow-scenario-simulated-gps`.

The frozen scenario's legacy ISBDA images were selected for the replaced
three-class detector and are outside the RescueNet segmentation model's
validation domain. They verify transport and inference wiring, not current
model accuracy.

## Redistribution note

No `LICENSE` file was present in the reviewed upstream repository. Before
publishing publicly, confirm that the team may redistribute its code, weights,
sample imagery, and training evidence.
