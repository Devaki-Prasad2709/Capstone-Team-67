# Research comparison: sources and interpretation

The manuscript's comparison is qualitative. No competing model was trained or benchmarked during this document revision. Numerical superiority is not claimed.

| Source | Verified basis for the comparison | Interpretation in this paper |
|---|---|---|
| [RescueNet, Scientific Data](https://www.nature.com/articles/s41597-023-02799-4) | UAV scene annotations and semantic-segmentation evaluation. | Our converted instance polygons and AP metrics need a matched protocol before comparison with semantic results. |
| [Zou et al., Sensors](https://www.mdpi.com/1424-8220/24/13/4371) | Building instance segmentation, channel attention and neck modifications; evaluation on Yangbi earthquake UAV imagery. | Relevant detector research, with a different dataset and task scope. |
| [SpaceNet 8 official implementation](https://github.com/SpaceNetChallenge/SpaceNet8) | Building/road extraction and flood attribution; official links to the benchmark papers. | Our radiometric grid is a different output unit and is not a semantic flood-mapping benchmark result. |
| [Selvakumaran et al., author institutional archive](https://elib.dlr.de/217980/1/1-s2.0-S2590061725000018-main.pdf) | Graph-based multimodal remote-sensing analysis for urban search and rescue. | Prior work already connects graphs and disaster evidence; our contribution is bounded to the implemented streaming and provenance workflow. |

The full references remain in the manuscript bibliography. The comparison table describes reported contributions; an omitted feature does not establish that another project lacks it.

The integration acceptance configuration and the active detector evaluation use different checkpoints. Removing repeated calendar dates from the prose does not merge those experiments. The manuscript retains that distinction, and its repository citation retains a stable evidence revision.
