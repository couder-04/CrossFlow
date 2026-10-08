# Final measured OCR results

Generated: `2026-09-30T08:32:58.100613+00:00`

Source: `reports/FINAL_RESULTS.json`. Rates are the stored fractions shown as percents.
**No fabricated metrics.**

- Measured only; no fabricated metrics.
- India default stack: india-v1 + plate_format=india, TTA and bbox pad OFF (row in_crops_india_v1_no_tta). TTA gained +1.0 pt (within the +/-2.2 pt 95% margin at n=1684) for ~7x OCR latency; the *_tta / *_tta_pad rows are kept for reference.
- OpenALPR >90% claim uses cct-s-v2-global-model, plate_format=none.
- HF README-only corpora (indian_anpr_corpus, indian_synth_sample) skipped — no images.
- in_synth_kaggle_* removed: it scored india-v1 on the abtexp synthetic set it was trained on. All 273 plates of <=10 chars were training images (271 read correctly); all 127 plates of 11 chars cannot be emitted by the 10-slot model (0 correct). The number and the per-state table measured the share of 11-char plates per state, not accuracy. in_crops is the valid held-out Indian test set.

| tag | n | found | exact | exact_o0 | char_acc | extra | ms_med | ms_p95 | failure_count |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| openalpr_eu_global | 108 | 100.0% | 92.6% | 99.1% | 98.8% | 10 | 249.9 | 328.0 | 8 |
| openalpr_br_global | 114 | 100.0% | 98.2% | 98.2% | 99.6% | 18 | 263.2 | 368.8 | 2 |
| openalpr_us_global | 222 | 100.0% | 87.8% | 90.5% | 97.0% | 12 | 243.2 | 296.2 | 27 |
| openalpr_eu_br_us_global | 444 | 100.0% | 91.7% | 94.6% | 98.1% | 40 | 249.9 |  |  |
| in_crops_india_v1_no_tta | 1684 | 100.0% | 73.0% | 73.1% | 92.9% | 0 | 33.3 | 40.5 | 455 |
| in_crops_india_v1_tta | 1684 | 100.0% | 74.0% | 74.2% | 93.3% | 0 | 227.5 | 272.1 | 438 |
| in_crops_global | 1684 | 100.0% | 31.1% | 37.2% | 77.3% | 0 | 23.4 | 28.0 | 1160 |
| in_full_india_v1_tta_pad | 25 | 96.0% | 60.0% | 60.0% | 77.1% | 0 | 768.0 | 2930.3 |  |
| openalpr_eu_india_v1_cross | 108 | 100.0% | 0.0% | 0.0% | 52.3% | 15 | 250.5 | 316.7 | 108 |
| openalpr_br_india_v1_cross | 114 | 100.0% | 0.0% | 0.0% | 65.4% | 21 | 247.2 | 312.3 | 114 |
| openalpr_us_india_v1_cross | 222 | 100.0% | 0.0% | 0.0% | 49.0% | 15 | 245.4 | 309.9 | 222 |
