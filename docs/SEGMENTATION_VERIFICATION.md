# YOLO26s segmentation verification

Verified on 2026-09-30 against the active scenario branch and the held-out
RescueNet test split.

## Checkpoint identity

- Active checkpoint: `ai/computer_vision/artifacts/drone_detector/weights/best.pt`
- SHA-256: `2d687e94fa5c2ef445c0888794de99ea1155d880ddb9be8f9237617eb55068b5`
- The hash exactly matches the training run's `rescuenet_yolo26s_seg_final/weights/best.pt`.
- Embedded task: `segment`
- Architecture: YOLO26s-seg, 11,422,286 parameters, 34.3 GFLOPs
- Embedded classes match the ten-class RescueNet dataset configuration.

## Fresh held-out test evaluation

The active checkpoint was evaluated on all 450 test images containing 2,680
labeled instances, at 640 pixels, batch size 1, on CPU.

| Output | Precision | Recall | mAP50 | mAP50-95 |
| --- | ---: | ---: | ---: | ---: |
| Boxes | 0.675 | 0.647 | 0.685 | 0.494 |
| Masks | 0.664 | 0.657 | 0.675 | 0.443 |

Measured mean speed was 1.5 ms preprocessing, 148.8 ms inference, and 8.3 ms
postprocessing per image on the local 11th-generation Intel i5 CPU.

## Polygon geometry check

For labeled test image `10794.jpg` (4000 × 3000):

- `building_total_destruction`: confidence 0.852299, 121 polygon points,
  valid/in-bounds polygon, best same-class ground-truth IoU 0.9637.
- `tree`: confidence 0.272847, 86 polygon points, valid/in-bounds polygon,
  best same-class ground-truth IoU 0.7426.
- Through the production worker's 640 × 640 preprocessing path, the destruction
  prediction remains at confidence 0.831008 with 147 polygon points and IoU
  0.9582 after mapping coordinates back to the source image.

The event contract intentionally retains polygons only for damage-relevant
classes: minor/major/total building damage and blocked roads. Context classes
such as trees and water retain boxes and confidence but omit polygon payloads.

## Scenario replay interpretation

The frozen Louisiana scenario uses three legacy ISBDA images selected for the
previous three-class detector. With the RescueNet segmentation checkpoint, the
live replay produced zero detections for `5_9240.jpg` and one `water` detection
each for `6_270.jpg` and `8_90.jpg`. These are not useful damage observations.
They demonstrate that the live Kafka/checkpoint/provenance path works, but they
must not be reported as validation of segmentation accuracy. A representative
scenario demonstration should replace those three frozen images with checksum-
locked RescueNet images and update their disclosed provenance.

## Integration checks

- Real checkpoint → polygon event → observation → graph smoke test passed.
- Three segmentation contract regressions passed.
- Scenario, operational dashboard, and segmentation focused suite: 18 passed.
- Full scenario replay completed 12/12 events with zero operational errors.

Preview: `runs/verification/10794_prediction_small.jpg`.
