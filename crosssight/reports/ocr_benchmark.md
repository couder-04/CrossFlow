# OCR benchmark results (measured)

Generated: `2026-09-30T07:25:51.263836+00:00`

Numbers below are copied from `reports/ocr_benchmark.json`.
**No fabricated metrics.**

Device: CPU/auto via onnxruntime (macOS CoreML/CPU)

## openalpr_global

- OCR model: `cct-s-v2-global-model`
- Plate format: `none`

| set | plates | found | exact | exact (O=0) | char acc | extra | ms med | ms p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| eu | 108 | 100.0% | 92.6% | 99.1% | 98.8% | 10 | 147.4 | 227.9 |
| br | 114 | 100.0% | 98.2% | 98.2% | 99.6% | 18 | 141.8 | 285.3 |
| us | 222 | 100.0% | 87.8% | 90.5% | 97.3% | 12 | 142.1 | 389.4 |

## india_v1_format

- OCR model: `india-v1`
- Plate format: `india`

| set | plates | found | exact | exact (O=0) | char acc | extra | ms med | ms p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| in_full | 25 | 80.0% | 64.0% | 64.0% | 74.9% | 3 | 98.8 | 156.0 |
| in_crops | 1684 | 100.0% | 72.7% | 72.8% | 92.7% | 0 | 23.0 | 28.5 |

## india_crops_global_baseline

- OCR model: `cct-s-v2-global-model`
- Plate format: `none`

| set | plates | found | exact | exact (O=0) | char acc | extra | ms med | ms p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| in_crops | 1684 | 100.0% | 31.1% | 37.2% | 77.3% | 0 | 16.0 | 20.0 |

## openalpr_india_v1_cross

- OCR model: `india-v1`
- Plate format: `india`

| set | plates | found | exact | exact (O=0) | char acc | extra | ms med | ms p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| eu | 108 | 100.0% | 0.0% | 0.0% | 53.9% | 15 | 175.3 | 356.2 |
| br | 114 | 100.0% | 0.0% | 0.0% | 67.7% | 21 | 214.4 | 557.9 |
| us | 222 | 100.0% | 0.0% | 0.0% | 42.8% | 15 | 170.8 | 306.7 |
