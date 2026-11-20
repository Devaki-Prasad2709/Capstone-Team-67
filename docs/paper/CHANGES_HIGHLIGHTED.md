# IEEE manuscript: highlighted corrections

This report compares the supplied manuscript with [the corrected IEEE manuscript](manuscript.tex), initially introduced in commit `e79f828` and subsequently expanded for the six-page revision. It explains additions, removals and their evidence. **Bold text identifies additions or corrected claims**; ~~strikethrough identifies removed claims~~. Wording below is condensed where indicated rather than a complete line-by-line diff.

The [highlighted LaTeX entry point](manuscript_highlighted.tex) renders substantive corrected passages in blue. This Markdown report also records deleted material and formatting changes that blue highlighting cannot show.

## Latest revision: research comparison and six-page target

- **Removed calendar-date and "historical" phrasing from the manuscript.** Evaluation scope and checkpoint differences remain explicit.
- **Added a comparison subsection and a full-width table** covering RescueNet, Zou et al., SpaceNet 8, Selvakumaran et al., and this prototype.
- **Explained why semantic IoU, instance AP, grid-change counts and operational graph results cannot be ranked as interchangeable accuracy scores.**
- **Added evidence identity and evaluation protocol discussion**, including content/event identity, component versus system validation, and missing latency terms.
- **Corrected the malformed revision macro**: its argument count must be `[1]`; the prior blanket citation replacement had mistakenly changed that syntax.
- Added substantive research discussion without changing IEEE margins or font sizes. See the README for final build status.

## 1. Title: remove an unsupported timing claim

Before:

> Real-Time AI-Powered Post-Disaster Area Scanning and Assessment System: A Provenance-Aware Multimodal Streaming Prototype

After:

> ~~Real-Time~~ AI-Powered Post-Disaster Area Scanning and Assessment System: A Provenance-Aware Multimodal Streaming Prototype

The available measurements do not establish an end-to-end latency guarantee. Event-driven processing alone does not establish real-time performance.

## 2. Citations: use IEEE automatic numbering

**Replaced manually typed reference numbers with LaTeX citation keys.**

| Original text | Corrected LaTeX |
|---|---|
| `[1]` | `\cite{resc}` |
| `[2]` | `\cite{zou}` |
| `[3]` | `\cite{spacenet}` |
| `[4]` | `\cite{selva}` |
| `[5]` | `\cite{gat}` |
| `[6]` | `\cite{gru}` |
| `[1]--[4]` | `\cite{resc,zou,spacenet,selva}` |

This allows numbering and grouped citations to follow the bibliography automatically. The implementation evidence now also uses **`\cite{team67}`**.

## 3. Architecture: replace the missing figure

Removed the dependency on ~~`ieee_architecture.png`~~, which was absent from the repository.

**Added a self-contained LaTeX architecture diagram** showing source adapters, Kafka/MinIO, Spark validation, drone/telemetry graph processing, satellite/social evidence, and the dashboard. The caption explains the separate evidence paths and backend operator controls.

**Added the reviewed repository revision `d8030798662c`** to the architecture introduction so implementation claims have a reproducible reference.

## 4. Satellite method: specify scoring and unknown behavior

Before, the text mentioned normalized difference and unspecified thresholds.

The corrected method explicitly states:

- **PRE and POST use aligned luminance values on a common 0-255 scale.**
- **Only pixels valid in both rasters contribute to the score.**
- **The default minimum valid fraction per cell is 0.95.**
- **Cells below that fraction are unknown and receive no change score.**
- **Low: score < 0.15; moderate: 0.15 <= score < 0.40; severe: score >= 0.40.**
- **These thresholds are heuristic and are not calibrated damage thresholds.**

Evidence: [broad_area_classifier.py](../../core/satellite/broad_area_classifier.py).

## 5. Satellite results: distinguish recorded evaluation evidence from current validation

Before, the manuscript called the 33 changed cells an output of the current procedure.

Corrected interpretation:

> **The recorded integration evaluation** reports 64 scored cells and 33 changed cells: 24 moderate and nine severe. **These describe that recorded run, not a newly executed validation of the current Universal Satellite Adapter.**

The counts were retained with their correct scope; they were not replaced with a newly invented measurement.

Removed the unsupported example:

> ~~12 of 16 cells scored, four withheld, 92.769% valid coverage, and scores approximately 0.0873-0.1225.~~

No supporting versioned artifact was established for those numbers.

The local untracked October result reports 32 changed cells, including 23 moderate and nine severe. That file was not included in the manuscript commit. **A current-adapter evaluation needs a validated, versioned artifact with source checksums, normalization settings and software revision before its numbers replace the recorded-evaluation paragraph.**

The paper retains the limitation that 22 flooded reference features out of 40 cannot be compared directly with grid-cell counts to calculate accuracy.

Evidence: [integration acceptance record](../END_TO_END_ACCEPTANCE.md).

## 6. YOLO integration: identify the checkpoint actually tested

Added:

> **The integration acceptance configuration used a three-class YOLO checkpoint; the separate detector evaluation uses the active ten-class YOLO26s-seg checkpoint.**

This prevents workflow acceptance from being read as evaluation of the active model. Current held-out metrics and the poor transfer to three legacy ISBDA images are attributed to the separate segmentation verification record.

Evidence: [acceptance record](../END_TO_END_ACCEPTANCE.md), [segmentation verification](../SEGMENTATION_VERIFICATION.md), and [checkpoint manifest](../../ai/computer_vision/artifact_manifest.json).

## 7. Social matching: remove a misleading scenario example

Removed:

> ~~A documented example resolved a Louisiana distress candidate to infrastructure node 6 with a heuristic match score of 0.4.~~

That example belongs to an optional legacy smoke-test fixture, rather than the active scenario being discussed.

Replaced with:

> **GIS match scores are heuristic association signals; no held-out NLP accuracy, F1 score, or probability calibration is established.**

Evidence: [legacy NLP test](../../tests/test_nlp_alert_state.py).

## 8. Scenario review: clarify how decisions occur

Added:

> **Responder-decision assets describe the scenario story and provenance; actual confirmation or rejection is performed through the operator review endpoint. The timeline milestone does not itself publish an automatic responder decision.**

This distinguishes a registered scenario asset from an actual responder action.

## 9. Bibliography and IEEE source formatting

| Change | Purpose |
|---|---|
| **Added GRU DOI `10.3115/v1/D14-1179`.** | Complete the existing reference. |
| **Replaced the vague internal dossier entry with a repository URL, branch, revision and access date.** | Make implementation evidence traceable. |
| **Reduced provenance-table column widths and removed outer padding.** | Leave room for spacing within an IEEE column. |
| **Added line-break opportunities to the full SHA-256 hash.** | Preserve the exact hash while allowing wrapping. |
| Removed unused `multirow` and `\IEEEoverridecommandlockouts`. | Remove unused preamble elements. |
| **Added a conditional blue revision macro and a separate review entry point.** | Maintain clean and highlighted versions from the same manuscript source. |

## 10. Verified information retained

The revisions do not change the reported held-out YOLO metrics, checkpoint hash, ten classes, CPU timings, or observed poor ISBDA transfer. They retain the GAT-GRU architecture, synthetic training limitations, uncalibrated relative-risk interpretation, simulated scenario fields, and the separate satellite evidence path.

No model training, application code or evaluation measurements were changed by the manuscript correction.

## 11. Validation limits

Citation-key resolution, matching LaTeX environment counts, removal of manually numbered citations, removal of external-image dependencies and whitespace were checked for the manuscript package.

**Both clean and highlighted PDFs now compile successfully and contain exactly six pages**, verified with Tectonic 0.17.0 and PDF page-count inspection. Times-compatible fonts are explicitly configured. The comparison table and representative pages were visually inspected. Underfull boxes and a sub-point bibliography-balancing warning remain; no horizontal overflow was reported. Venue-specific submission requirements still need checking.

Build instructions: [paper README](README.md).
