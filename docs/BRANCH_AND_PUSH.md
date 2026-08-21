# Create a branch and push the combined codebase

Git is initialized locally on the unborn `main` branch. Create your own branch,
make the first commit, and connect your repository:

```powershell
git switch -c your-name/integrated-pipeline
git add .
git commit -m "Integrate streaming pipeline and YOLO AI layer"
git remote add origin https://github.com/YOUR-ACCOUNT/YOUR-REPOSITORY.git
git push -u origin your-name/integrated-pipeline
```

If you copy the files elsewhere, initialize that directory with `git init -b
main` first. `.env`, environments, local runtimes, logs, Parquet/checkpoint
output, and dedup state are ignored. Each preserved checkpoint is below
GitHub's 100 MB per-file limit.
