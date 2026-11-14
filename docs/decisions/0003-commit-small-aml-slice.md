# 0003: Commit one small synthetic AML slice

**What:** Track `data/aml_slice_hi_small.csv` (2.3 MB, synthetic) in git. Everything else under `data/` stays ignored, and the full IBM files (`data/raw/`, `*Trans.csv`, `*Patterns.txt`) are explicitly ignored.

**Why:** The slice is small, synthetic and needed in every Codespace, in CI and in the deployed demo. Keeping it in the repo makes runs reproducible without a separate download step.

**Rule change:** CLAUDE.md said no data files are committed. The exception is this slice and any other synthetic slice under 10 MB that is listed in data/README.md. Anything larger needs a decision first.

**Alternatives:**
- Load it from Kaggle or object storage at setup time: more moving parts, and CI would need credentials.
- Keep it out of git and only load it into Neon: loses reproducibility for evals.

**Open:** the dataset license must be copied into data/README.md before this is public. Check it allows redistributing a derived slice and what attribution it needs.
