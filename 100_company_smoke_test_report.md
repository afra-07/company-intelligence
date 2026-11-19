# 100-Company Smoke Test Report

**Date:** 19 November 2026  
**Input File:** `org_numbers.txt`  
**Output File:** `results_100.jsonl`  
**Execution Command:** `python run.py org_numbers.txt --output results_100.jsonl --concurrency 5 --refresh`  

---

## Dataset & Scope Notice

> [!NOTE]
> **Dataset Origin:** This dataset was assembled locally as a smoke test using 100 organisation numbers derived directly from existing local profile filenames in the `./profiles` directory. It is **not** an official Builderr-supplied evaluation batch.
> 
> **Data Accuracy Disclaimer:** Achieving 100% execution success demonstrates pipeline stability, concurrency resilience, contract adherence, and successful refresh/merge operations under the CLI runner. It does **not** claim that all company facts were independently verified against ground truth or represent 100% real-world data accuracy.

---

## Execution Summary

| Metric | Result |
| :--- | :--- |
| **Total Records Processed** | 100 |
| **Unique Organisation Numbers** | 100 |
| **Successful Profiles** | 100 |
| **Failed Profiles** | 0 |
| **Success Rate** | 100% |
| **Contract Violations** | 0 |
| **Concurrency Level** | 5 |
| **Mode** | Force Refresh (`--refresh`) |

---

## Incident & Remediation: Input Encoding (UTF-8 BOM)

During the initial run, the first organisation number failed input validation due to a leading byte order mark (BOM, `\ufeff`) present in the locally created text file.

- **Issue:** The first line contained `\ufeff` prepended to the 9-digit organisation number, causing input length and digit validation to fail.
- **Remediation:** The input file was re-saved in standard UTF-8 encoding without BOM.
- **Subsequent Run:** The re-executed run with clean UTF-8 encoding proceeded without interruption, achieving **100/100 successful profiles** and **0 contract violations**.
