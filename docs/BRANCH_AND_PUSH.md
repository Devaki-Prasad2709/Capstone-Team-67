# Work with the combined branch

The combined codebase is published from the team repository's unchanged
`main` commit to this dedicated branch:

```text
dibsei/integrated-disaster-streaming-system
```

Check it out from another clone with:

```powershell
git fetch origin
git switch dibsei/integrated-disaster-streaming-system
```

Commit and push later changes without updating `main`:

```powershell
git add .
git commit -m "Describe the change"
git push
```

`.env`, environments, local runtimes, logs, Parquet/checkpoint output, and
dedup state are ignored. Each preserved checkpoint is below GitHub's 100 MB
per-file limit.
