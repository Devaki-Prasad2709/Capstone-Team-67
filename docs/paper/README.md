# IEEE manuscript and revision record

This package corrects the supplied IEEE conference manuscript against repository revision `d8030798662ce5340f9e8f42e0967b630477868d`.

- `manuscript.tex`: clean IEEEtran conference manuscript.
- `manuscript_highlighted.tex`: review entry point; substantive corrected passages appear in blue.
- `CHANGES_HIGHLIGHTED.md`: before/after corrections and the latest research expansion.
- `RESEARCH_COMPARISON.md`: source links and comparison limits.
- `manuscript.pdf` and `manuscript_highlighted.pdf`: compiled reading and review copies.

## Build

Upload both TeX files to Overleaf, or use a TeX distribution with IEEEtran, fontenc, mathptmx, cite, amsmath, amssymb, graphicx, booktabs, array, url, balance, microtype and xcolor. T1 encoding and mathptmx provide Times-compatible text and mathematics across the build engines.

From this directory:

```sh
pdflatex manuscript.tex
pdflatex manuscript.tex
pdflatex manuscript_highlighted.tex
pdflatex manuscript_highlighted.tex
```

Select `manuscript.tex` as the main file for submission, or `manuscript_highlighted.tex` for review. No external figure is required. The architecture diagram uses native LaTeX boxes, text and arrows.

IEEEtran supplies the two-column conference layout, author blocks, abstract, keywords, numbered sections, captions and bibliography. Final page limits, anonymity, copyright notices and PDF compliance depend on the target conference. No conference-specific compliance is claimed.

## Research expansion

The revised related-work section includes a comparison table and discusses task scope, label representation, comparable metrics, generalization and operational evaluation. An additional methods subsection explains evidence identity and evaluation protocol. Calendar-date-heavy acceptance wording has been removed while preserving experiment and checkpoint distinctions. No new experiments or benchmark improvements are claimed.

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
| Satellite results | Attributed the 33-cell result to the recorded integration evaluation. | It must not be presented as a current universal-adapter measurement. |
| Satellite results | Removed the unsupported 4x4, 92.769% coverage and score-range example. | No supporting versioned artifact was established. |
| Integration results | Identified the integration configuration's three-class checkpoint. | Separates workflow acceptance from active ten-class model evidence. |
| Bibliography | Added the GRU DOI and replaced the vague internal dossier with a cited, revision-pinned repository reference. | Improves attribution and reproducibility. |
| Preamble | Removed unused multirow and funding override; added a review-color macro. | Keeps the clean and highlighted documents on one shared source. |

## Evidence and interpretation

The held-out YOLO box/mask metrics, full checkpoint hash, architecture, CPU timings and poor transfer to the three ISBDA images are retained from:
- `../../ai/computer_vision/artifact_manifest.json`
- `../SEGMENTATION_VERIFICATION.md`

Recorded replay and satellite counts come from:
- `../END_TO_END_ACCEPTANCE.md`

Current classifier settings come from:
- `../../core/satellite/broad_area_classifier.py`

The separate local, untracked `results/satellite_e2e_2026-10-01.json` reports 32 changed cells (23 moderate, 9 severe). It is not included or promoted to authoritative published evidence by this documentation commit. The manuscript instead explicitly limits its numerical satellite claim to the committed acceptance record. A new current-adapter result should be validated and versioned before replacing that paragraph.

This paper evaluates the Louisiana replay. It does not claim to comprehensively evaluate the later Mexico scenario or every subsequent dashboard feature.

## Validation and remaining checks

Source checks cover matched LaTeX environments, resolved citation keys, absence of manual numbered citations, absence of external image dependencies, and whitespace errors.

Both entry points compiled successfully with Tectonic 0.17.0. Both PDFs contain exactly six pages, including references, using the standard IEEEtran conference layout and Times-compatible fonts. No manual page breaks, font shrinking or margin changes were used to reach the target. The comparison table and representative rendered pages were visually inspected. Citation and reference resolution passed.

The compiler reports underfull text boxes and a sub-point vertical-box warning during bibliography balancing; no horizontal overflow was reported. These are layout warnings, not compilation failures. Final venue-specific PDF compliance and layout should still be checked before submission. Rebuilding with another TeX distribution can change line or page breaks.

No model training, application behavior, or evaluation measurements were changed.
