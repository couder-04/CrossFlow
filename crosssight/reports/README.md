# Reports

Checked-in summaries:

- `ocr_benchmark.md` and `ocr_benchmark.json` — OCR benchmark tables. Regenerate with `make benchmark-ocr`.
- `FINAL_RESULTS.md` and `FINAL_RESULTS.json` — the numbers the README cites.
- `scenario_proof.json` — alert-pipeline proof. Regenerate with `make prove-scenarios`.
- `city_load_summary.json` — city-load headline numbers. Regenerate with `make city-load`.

Per-run logs (`city_load_run_A.log`, `_A2`, `_B`, `_C`) and `video_demo_annotated2.mp4` live in `artifacts/`, which is gitignored. Copy them there after a local run if you want to keep the raw output.
