# Indian footage evaluation

Drop CCTV or ANPR clips into one of `day/`, `night/`, `toll/`, `highway/`, `urban/`, or `gantry/`.
Optional `gt.csv` columns: `image_or_video`, `plate`.

```bash
uv run python evaluation/indian/run_eval.py --out reports/indian_eval.json
```

The report lists media counts. Exact-match and character accuracy appear only when both a prediction file and ground truth are supplied. This pipeline does not train on the clips and does not claim accuracy for empty scenes.
