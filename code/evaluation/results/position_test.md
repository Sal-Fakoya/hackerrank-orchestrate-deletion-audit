# Shared-run position test (dev gold only)

## 1. Owner order across record types within a shared run

Type pairs with >= 2 dev-labelled owners in common: 10; same order: 4; different order: 6.

- account owners ['case_28', 'case_27', 'case_29'] vs analytics owners ['case_27', 'case_28']
- account owners ['case_28', 'case_27', 'case_29'] vs data_flow owners ['case_27', 'case_28']
- analytics owners ['case_27', 'case_28'] vs support owners ['case_28', 'case_27']
- analytics owners ['case_27', 'case_28'] vs backup owners ['case_28', 'case_27']
- support owners ['case_28', 'case_27'] vs data_flow owners ['case_27', 'case_28']
- data_flow owners ['case_27', 'case_28'] vs backup owners ['case_28', 'case_27']

## 2. Rank attribution: i-th record of a type -> i-th member request

| ordering of member requests | correct | wrong | accuracy |
|---|---|---|---|
| requested_at | 8 | 16 | 33% |
| case number | 7 | 17 | 29% |
| request email | 10 | 14 | 42% |
| audit_at | 7 | 17 | 29% |

Best ordering: **request email**.

## 3. Dev impact (latest saved agent dev run of the final version = run B, rescored; no model calls)

| metric | current (reject identity-free shared-run records) | with position attribution |
|---|---|---|
| status accuracy | 100.0% | 95.0% |
| step precision | 96.2% | 80.0% |
| step recall | 85.6% | 88.1% |
| step F1 | 90.6% | 83.9% |
| exact sequence | 45.0% | 50.0% |
| resource precision | 98.2% | 81.1% |
| resource recall | 80.0% | 85.7% |

Records added by position attribution to dev cases: 17 (4 owned by that case per dev gold, 13 not):

- case_09: account_3960 (gold owner: case_09)
- case_09: analytics_5718 (gold owner: not in any dev answer)
- case_09: support_6334 (gold owner: not in any dev answer)
- case_09: data_flow_6846 (gold owner: not in any dev answer)
- case_09: backup_3933 (gold owner: not in any dev answer)
- case_17: account_9494 (gold owner: not in any dev answer)
- case_21: account_7015 (gold owner: not in any dev answer)
- case_22: account_4990 (gold owner: case_22)
- case_23: account_9155 (gold owner: not in any dev answer)
- case_26: account_3136 (gold owner: not in any dev answer)
- case_26: analytics_1895 (gold owner: not in any dev answer)
- case_26: tombstone_3963 (gold owner: not in any dev answer)
- case_27: account_5482 (gold owner: case_28)
- case_27: analytics_4574 (gold owner: case_27)
- case_27: support_2351 (gold owner: case_28)
- case_27: backup_838 (gold owner: case_28)
- case_29: account_7012 (gold owner: case_29)
