# IEEE manuscript and revision record

This package corrects the supplied IEEE conference manuscript against repository revision `d8030798662ce5340f9e8f42e0967b630477868d`.

- `manuscript.tex`: clean IEEEtran conference manuscript.
- `manuscript_highlighted.tex`: review entry point; substantive corrected passages appear in blue.
- This file records all changes, including deletions and formatting changes that cannot be shown by blue text.

## Build

Upload both TeX files to Overleaf, or use a TeX distribution with IEEEtran, cite, amsmath, amssymb, graphicx, booktabs, array, url, balance, microtype and xcolor.

From this directory:

```sh
pdflatex manuscript.tex
pdflatex manuscript.tex
pdflatex manuscript_highlighted.tex
pdflatex manuscript_highlighted.tex
```

Select `manuscript.tex` as the main file for submission, or `manuscript_highlighted.tex` for review. No external figure is required. The architecture diagram uses native LaTeX boxes, text and arrows.

IEEEtran supplies the two-column conference layout, author blocks, abstract, keywords, numbered sections, captions and bibliography. Final page limits, anonymity, copyright notices and PDF compliance depend on the target conference. No conference-specific compliance is claimed.

## Highlighted change record

| Location | Correction | Reason |
|---|---|---|
| Title | Removed “Real-Time”. | No deadline or end-to-end latency validation supports that claim. |
| Throughout | Replaced manually typed citation numbers with keyed cite commands. | Automatic IEEE numbering and citation grouping. |
| Architecture opening | Added a repository revision and implementation citation. | Makes the reviewed code and evidence traceable. |
| Architecture figure | Replaced the missing PNG with a self-contained diagram and corrected caption. | Removes the missing-image compile dependency and explains the actual evidence paths. |
| Provenance table | Reduced column widths and removed outer padding. | Allows room for intercolumn spacing in the IEEE column. |
| Detector methods | Added break opportunities in the full checkpoint hash. | Preserves the exact hash while allowing narrow-column wrapping. |
| Satellite methods | Defined the shared luminance scale and intersection of valid masks. | Specifies what the score actually measures. |
| Satellite methods | Added thresholds 0.15/0.40 and default minimum cell coverage 0.95. | Reproducible classification and unknown policy. |
| Social methods | Removed the node-6 / 0.4 example. | It came from an optional legacy smoke-test fixture, not the active scenario. |
| Scenario methods | Clarified that responder decisions occur through the operator endpoint. | Registered story assets do not automatically confirm reports. |
| Satellite results | Explicitly dated the 33-cell result as historical acceptance evidence. | It must not be presented as a current universal-adapter measurement. |
| Satellite results | Removed the unsupported 4x4, 92.769% coverage and score-range example. | No supporting versioned artifact was established. |
| Integration results | Identified the historical run's replaced three-class checkpoint. | Separates workflow acceptance from active ten-class model evidence. |
| Bibliography | Added the GRU DOI and replaced the vague internal dossier with a cited, revision-pinned repository reference. | Improves attribution and reproducibility. |
| Preamble | Removed unused multirow and funding override; added a review-color macro. | Keeps the clean and highlighted documents on one shared source. |

## Evidence and interpretation

The held-out YOLO box/mask metrics, full checkpoint hash, architecture, CPU timings and poor transfer to the three ISBDA images are retained from:
- `../../ai/computer_vision/artifact_manifest.json`
- `../SEGMENTATION_VERIFICATION.md`

Historical replay and satellite counts come from:
- `../END_TO_END_ACCEPTANCE.md`

Current classifier settings come from:
- `../../core/satellite/broad_area_classifier.py`

The separate local, untracked `results/satellite_e2e_2026-10-01.json` reports 32 changed cells (23 moderate, 9 severe). It is not included or promoted to authoritative published evidence by this documentation commit. The manuscript instead explicitly limits its numerical satellite claim to the committed historical record. A new current-adapter result should be validated and versioned before replacing that paragraph.

This paper evaluates the Louisiana replay. It does not claim to comprehensively evaluate the later Mexico scenario or every subsequent dashboard feature.

## Validation and remaining checks

Source checks cover matched LaTeX environments, resolved citation keys, absence of manual numbered citations, absence of external image dependencies, and whitespace errors.

A TeX compiler is unavailable in the editing environment, so PDF compilation, page count, diagram layout, overfull-box warnings and visual typography have not been verified. Build both entry points and inspect the rendered PDFs before submission. Source checks are not a substitute for that review.

No model training, application behavior, or evaluation measurements were changed.
