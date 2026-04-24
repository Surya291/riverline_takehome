# Dev Folder Guide

This folder contains all analysis work used to build the submission (`eval_takehome.py`, `violations.md`, `writeup.md`).

If you are new to this repo, follow this file top-to-bottom.

---

## 1) What this folder is for

`dev/` is a working area for:
- running evaluator experiments,
- validating violations against outcomes,
- borrower segment analysis,
- annotator disagreement analysis,
- findings generation with evidence tables.

Think of it as the analysis pipeline that feeds the final report files at repo root.

---

## 2) Quick navigation (what to open first)

1. `run_evals.ipynb`  
   Main notebook for evaluation + analysis orchestration.

2. `test.ipynb`  
   Evaluator test notebook (iterative experiments, sanity checks, quick validations).

3. `conversation_bundle_flat.csv`  
   Joined dataset used by multiple scripts (logs + outcomes + annotation fields).

4. `eval_v2.jsonl`  
   Evaluator output used as primary input for downstream analysis.

---

## 3) Pipeline overview (recommended order)

Use this order to replicate the full analysis:

1. **Generate evaluator outputs**
   - Input: `problem_statement/data/production_logs.jsonl`
   - Output: `eval_v2.jsonl`
   - Notebook/script: `test.ipynb` or `run_evals.ipynb`

2. **Run rule-outcome correlation**
   - Script: `violation_correlation_simple.py` (stable baseline)
   - Output: `violation_correlation_results.csv`, `risk_difference_plot.png`, `violation_outcome_heatmap.png`

3. **Run segment analysis**
   - Script: `segment_violation_analysis.py`
   - Output folder: `segment_analysis_output/`
   - Includes summaries, stats, rule matrices, example registries, and charts.

4. **Run annotator disagreement analysis**
   - Script: `annotator_disagreement_analysis.py`
   - Output folder: `annotator_diff_output/`

5. **Run findings pipelines**
   - Script A: `findings_evidence_pipeline.py` -> `findings_output/`
   - Script B: `findings_deep_analysis.py` -> `findings_deep_output/`

6. **Use outputs to write final docs**
   - Root outputs: `violations.md`, `writeup.md`, `FINDINGS_DEEP.md`, etc.

---

## 4) Folder map by purpose

### Core notebooks
- `run_evals.ipynb` - main execution and analysis flow
- `test.ipynb` - evaluator testing / ad hoc analysis
- `explore_data.ipynb` - early data exploration
- `annotator_diff.ipynb` - annotator-focused notebook exploration

### Core scripts
- `segment_violation_analysis.py` - segment-wise + rule-first analysis
- `violation_correlation_simple.py` - robust rule-outcome correlations
- `violation_correlation_analysis.py` - alternate correlation script
- `annotator_disagreement_analysis.py` - annotator personality + disagreement metrics
- `findings_evidence_pipeline.py` - findings artifact generation (v1)
- `findings_deep_analysis.py` - deeper findings pipeline (v2+)
- `visualise_conv.py` - conversation visualization helper

### Key data artifacts
- `conversation_bundle_flat.csv` - merged analysis table
- `eval_v1.jsonl`, `eval_v2.jsonl` - evaluator outputs

### Output directories
- `segment_analysis_output/` - segment summaries, stats, examples, charts
- `annotator_diff_output/` - disagreement metrics and examples
- `findings_output/` - findings artifacts (v1 pipeline)
- `findings_deep_output/` - findings artifacts (deep pipeline)

### Standalone output files
- `violation_correlation_results.csv`
- `score_analysis_results.csv`
- `risk_difference_plot.png`
- `violation_outcome_heatmap.png`

---

## 5) Which outputs are used in final submission

Most important for final reports:

- From `findings_deep_output/`:
  - `outcome_specific_rule_predictors.csv`
  - `violation_concentration_analysis.csv`
  - `rule_prioritization_matrix.csv`
  - `evidence_with_turns_deep.csv`

- From `segment_analysis_output/`:
  - `summary_*.csv`
  - `rule_segment_matrix.csv`
  - `examples_*.csv`

- From `annotator_diff_output/`:
  - `annotator_summary.csv`
  - `pairwise_disagreement_metrics.csv`
  - `variety_disagreement_examples.csv`

---

## 6) Repro tips

- Use the same Python environment where dependencies are available (pandas/matplotlib/etc.).
- Prefer running scripts first, then notebooks for inspection.
- If a script writes to an output folder, treat that folder as generated artifacts.
- Keep `eval_v2.jsonl` and `conversation_bundle_flat.csv` synchronized when rerunning.

---

## 7) Clutter notes (what can be cleaned later)

Safe cleanup candidates (if you want to tidy further later):
- `__pycache__/`
- duplicate/older experiment files once superseded
- intermediate images/CSVs not referenced by final docs

Do this only after confirming final report references are intact.

---

## 8) Minimal command checklist (example)

From repo root:

1. Run evaluator and generate/refresh `dev/eval_v2.jsonl`
2. Run:
   - `python dev/violation_correlation_simple.py`
   - `python dev/segment_violation_analysis.py`
   - `python dev/annotator_disagreement_analysis.py`
   - `python dev/findings_evidence_pipeline.py`
   - `python dev/findings_deep_analysis.py`
3. Use generated outputs to refresh `violations.md` and `writeup.md`

---

If you want this folder physically reorganized into subfolders like `scripts/`, `notebooks/`, `outputs/`, `data/`, do that in a separate refactor pass (with path updates in notebooks/scripts).
