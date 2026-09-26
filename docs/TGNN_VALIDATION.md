# TGNN inference validation and interpretation

## Verified inference contract

```text
ordered NetworkX snapshots
  -> fixed-extent position normalization
  -> PyG tensors
  -> committed TGNN checkpoint
  -> one output row per node and timestep
  -> graph node ID and original GIS source ID
```

The committed checkpoint is `tgnn/models/tgnn.pth`, SHA-256
`ad40a02e91cfe414da23f585dcf237d7fd2b5f646f3ebc47c20cb7d73640cc88`.
Its manifest is checked before inference.

For the Louisiana graph, every snapshot has:

- node tensor `[21, 7]`;
- edge index `[2, 322]`;
- edge attributes `[322, 3]`;
- node types `[21]`;
- normalized x/y coordinates in `[0, 1]`, using one extent for the sequence.

The exact node tensor order is:

```text
x_pos, y_pos, load, capacity, damage, stress, status
```

The first six values are inputs. `status` is excluded by `TGNN.forward()`
because it is the next-timestep supervised target. Node insertion order and edge
order must remain identical across snapshots. Known snapshot timestamps must be
strictly increasing.

## Correct output semantics

Training uses `status=1` for operational and `status=0` for failed. Therefore:

```text
operational score   = sigmoid(logit)
failure-risk score  = sigmoid(-logit) = 1 - sigmoid(logit)
```

The earlier adapter incorrectly called `sigmoid(logit)` “risk.” That is why all
baseline “risks” appeared very high: values around 0.96 were actually high
operational scores. The adapter now returns the complementary failure-risk score.

## Louisiana scenario response

For Estuary Road (`osm-way-791288888`):

| Snapshot | Damage | Stress | Failure-risk score | Rank of 21 |
|---|---:|---:|---:|---:|
| Baseline | 0.00 | 0.35 | 0.0242 | 19 |
| Degraded | 0.35 | 1.00 | 0.6979 | 1 |
| Recovery | 0.15 | 0.7018 | 0.2507 | 1 |

The road rises to the highest relative rank when degraded. Recovery lowers its
score substantially, although it stays above baseline and remains first because
the GRU retains temporal context. An isolated damage/stress increase, with load
unchanged, also moves this road from score `0.0242` to `0.1903` and rank 1.

All logits and scores are finite, and every output retains its tensor row,
integer graph node ID, stable GIS source ID, node type, and rank.

## Calibration limitation

These values are **not meaningful real-world probabilities**. The checkpoint:

- was trained on synthetic graphs and simulated next-status labels;
- has no held-out Louisiana failure labels;
- has no Platt, isotonic, temperature-scaling, or other calibration artifact;
- has no validated alert threshold or real-world reliability curve.

Present the output as an **uncalibrated relative failure-risk score**. It is
appropriate for ranking nodes and comparing a node across scenario time. Do not
say that `0.70` means a 70% chance of failure. Probability claims require a
labelled validation set representative of deployment, followed by calibration
and reliability/error analysis.
