## dev comparison (20 cases)

| metric | baseline | agent |
|---|---|---|
| status accuracy | 70.0% | 100.0% |
| step precision | 45.6% | 96.2% |
| step recall | 65.3% | 86.4% |
| step F1 | 53.7% | 91.1% |
| step F1 ignoring evidence_id | 65.5% | 91.1% |
| field accuracy: phase | 42.9% | 88.2% |
| field accuracy: system_id | 27.7% | 63.9% |
| field accuracy: resource_id | 25.0% | 63.9% |
| field accuracy: operation | 23.4% | 64.7% |
| field accuracy: state | 27.2% | 65.5% |
| field accuracy: evidence_id | 38.6% | 87.4% |
| sequence accuracy (exact) | 15.0% | 45.0% |
| resource precision | 63.4% | 98.3% |
| resource recall | 84.3% | 81.4% |
| model calls | 0 | 199 |

### Per case

| case | gold | baseline status | baseline F1 | baseline exact | agent status | agent F1 | agent exact |
|---|---|---|---|---|---|---|---|
| case_01 | COMPLETE | COMPLETE | 0.00 | no | COMPLETE | 1.00 | yes |
| case_04 | COMPLETE | COMPLETE | 0.67 | no | COMPLETE | 0.67 | no |
| case_05 | INCOMPLETE | INCOMPLETE | 1.00 | yes | INCOMPLETE | 1.00 | yes |
| case_06 | INCOMPLETE | INCOMPLETE | 1.00 | yes | INCOMPLETE | 1.00 | yes |
| case_07 | INCOMPLETE | COMPLETE | 0.00 | no | INCOMPLETE | 0.25 | no |
| case_09 | INCOMPLETE | INCOMPLETE | 0.62 | no | INCOMPLETE | 0.89 | no |
| case_10 | INCOMPLETE | INCOMPLETE | 0.25 | no | INCOMPLETE | 1.00 | yes |
| case_11 | INCOMPLETE | INCOMPLETE | 0.97 | no | INCOMPLETE | 1.00 | yes |
| case_12 | INCOMPLETE | INCOMPLETE | 0.40 | no | INCOMPLETE | 1.00 | yes |
| case_13 | INCOMPLETE | INCOMPLETE | 0.62 | no | INCOMPLETE | 1.00 | yes |
| case_15 | INCOMPLETE | NOT_APPLICABLE | 0.00 | no | INCOMPLETE | 1.00 | yes |
| case_17 | INCOMPLETE | INCOMPLETE | 1.00 | yes | INCOMPLETE | 0.91 | no |
| case_21 | COMPLETE | INCOMPLETE | 0.00 | no | COMPLETE | 0.50 | no |
| case_22 | INCOMPLETE | INCOMPLETE | 0.84 | no | INCOMPLETE | 0.93 | no |
| case_23 | INCOMPLETE | INCOMPLETE | 0.48 | no | INCOMPLETE | 0.93 | no |
| case_24 | INCOMPLETE | INCOMPLETE | 0.67 | no | INCOMPLETE | 1.00 | yes |
| case_26 | COMPLETE | NOT_APPLICABLE | 0.00 | no | COMPLETE | 0.80 | no |
| case_27 | COMPLETE | INCOMPLETE | 0.00 | no | COMPLETE | 0.80 | no |
| case_28 | COMPLETE | INCOMPLETE | 0.00 | no | COMPLETE | 0.80 | no |
| case_29 | INCOMPLETE | INCOMPLETE | 0.82 | no | INCOMPLETE | 0.96 | no |
