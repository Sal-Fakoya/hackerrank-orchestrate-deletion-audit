# Edge-case coverage (518 eval requests + all evidence, no LLM calls)

| pattern | occurrences | notes |
|---|---|---|
| prompt injection in request_text | 0 | regex over all 518 texts |
| prompt injection inside evidence | 0 | - |
| third-party request | 0 | - |
| withdrawn / cancelled deletion | 1 | case_542 (don't delete) |
| partial deletion of a single item | 1 | case_538 (only) |
| request email matches no identity | 0 | - |
| email historical for one user, current for another | 0 | - |
| retention end before the audit_at of a linked request | 0 | earliest stated retention end in evidence: 2031-12-31T23:59:59Z |
| backup restore stated as not blockable | 0 | - |
| deleted then rewritten by sync/index/restore (activity) | 56 | 56 resources; 32 'repopulated'/restore events |
| primary vs replica disagreement in one SQL snapshot | 13 | 11 files, 13 resources |

## Details

### Request-text matches (all)

**prompt injection in request_text:** none

**third-party request:** none

**withdrawn / cancelled deletion:** case_542 matched 'don't delete': Could you send me a copy of the information held on my account? I still use it, so please don't delete or change anything.

**partial deletion of a single item:** case_538 matched 'only': Please remove me from marketing lists only. I still want receipts and service messages for this account.

### Emails shared across users

- none

### Retention

- records with a stated retention end: 110; earliest end: 2031-12-31T23:59:59Z
- retention text in evidence_27: ORD-20264118 | user_08 | Card dispute | 04 May 2026 | USD 184.20 | Retain until 04 May 2033 | AR
- retention text in evidence_27: ORD-20264207 | user_12 | Tax correction | 09 May 2026 | EUR 72.40 | Retain until 09 May 2033 | ML
- retention text in evidence_27: ORD-20264319 | user_17 | Chargeback | 14 May 2026 | GBP 219.00 | Retain until 14 May 2033 | SK
- retention text in evidence_27: ORD-20264402 | user_21 | Invoice inquiry | 18 May 2026 | CAD 96.55 | Retain until 18 May 2033 | AR
- retention text in evidence_27: ORD-20264511 | user_24 | Card dispute | 23 May 2026 | AUD 311.80 | Retain until 23 May 2033 | ML
- retention text in evidence_27: ORD-20264600 | user_29 | Tax record | 28 May 2026 | USD 48.75 | Retain until 28 May 2033 | SK

### Deleted then rewritten (first 25)

- account_1334: delete activity_122 (SUCCESS) then activity_126 SYNC_RECORD: Replication worker reported its final checkpoint for account_1334.
- account_634: delete activity_174 (SUCCESS) then activity_177 SYNC_RECORD: Replication worker reported its final checkpoint for account_634.
- account_4974: delete activity_197 (SUCCESS) then activity_201 SYNC_RECORD: Replication worker reported its final checkpoint for account_4974.
- account_3626: delete activity_229 (SUCCESS) then activity_234 SYNC_RECORD: Replication worker reported its final checkpoint for account_3626.
- account_2424: delete activity_246 (SUCCESS) then activity_247 SYNC_RECORD: Replication worker reported its final checkpoint for account_2424.
- account_1035: delete activity_262 (PARTIAL) then activity_271 SYNC_RECORD: Replication worker reported its final checkpoint for account_1035.
- account_3119: delete activity_371 (PARTIAL) then activity_379 SYNC_RECORD: Replication worker reported its final checkpoint for account_3119.
- account_6654: delete activity_404 (PARTIAL) then activity_412 SYNC_RECORD: Replication worker reported its final checkpoint for account_6654.
- account_3205: delete activity_407 (SUCCESS) then activity_420 SYNC_RECORD: Replication worker reported its final checkpoint for account_3205.
- account_5293: delete activity_445 (SUCCESS) then activity_448 SYNC_RECORD: Replication worker reported its final checkpoint for account_5293.
- account_3183: delete activity_567 (SUCCESS) then activity_572 SYNC_RECORD: Replication worker reported its final checkpoint for account_3183.
- account_1538: delete activity_587 (PARTIAL) then activity_591 SYNC_RECORD: Replication worker reported its final checkpoint for account_1538.
- account_3653: delete activity_604 (PARTIAL) then activity_612 SYNC_RECORD: Replication worker reported its final checkpoint for account_3653.
- account_3002: delete activity_637 (SUCCESS) then activity_638 SYNC_RECORD: Replication worker reported its final checkpoint for account_3002.
- account_4921: delete activity_805 (FAILED) then activity_811 SYNC_RECORD: Replication worker reported its final checkpoint for account_4921.
- account_4204: delete activity_848 (PARTIAL) then activity_854 SYNC_RECORD: Replication worker reported its final checkpoint for account_4204.
- account_9975: delete activity_851 (SUCCESS) then activity_861 SYNC_RECORD: Replication worker reported its final checkpoint for account_9975.
- account_6784: delete activity_1031 (FAILED) then activity_1034 SYNC_RECORD: Replication worker reported its final checkpoint for account_6784.
- account_5389: delete activity_1049 (FAILED) then activity_1051 SYNC_RECORD: Replication worker reported its final checkpoint for account_5389.
- account_6246: delete activity_1078 (PARTIAL) then activity_1083 SYNC_RECORD: Replication worker reported its final checkpoint for account_6246.
- account_4446: delete activity_1119 (SUCCESS) then activity_1125 SYNC_RECORD: Replication worker reported its final checkpoint for account_4446.
- account_8105: delete activity_1235 (SUCCESS) then activity_1241 SYNC_RECORD: Replication worker reported its final checkpoint for account_8105.
- account_6539: delete activity_1275 (PARTIAL) then activity_1283 SYNC_RECORD: Replication worker reported its final checkpoint for account_6539.
- account_3204: delete activity_1310 (PARTIAL) then activity_1315 SYNC_RECORD: Replication worker reported its final checkpoint for account_3204.
- account_2475: delete activity_1445 (PARTIAL) then activity_1455 SYNC_RECORD: Replication worker reported its final checkpoint for account_2475.

### Repopulate / restore events

- activity_82: Active connector repopulated downstream resource search_2818.
- activity_138: Active connector repopulated downstream resource search_3382.
- activity_325: Active connector repopulated downstream resource search_8587.
- activity_338: Active connector repopulated downstream resource search_2591.
- activity_568: Active connector repopulated downstream resource search_8450.
- activity_732: Active connector repopulated downstream resource search_9100.
- activity_754: Active connector repopulated downstream resource search_3251.
- activity_773: Active connector repopulated downstream resource search_2886.
- activity_975: Active connector repopulated downstream resource search_3232.
- activity_1157: Active connector repopulated downstream resource search_7859.
- activity_1198: Active connector repopulated downstream resource search_496.
- activity_1220: Active connector repopulated downstream resource search_3355.
- activity_1415: Active connector repopulated downstream resource search_3773.
- activity_1448: Active connector repopulated downstream resource search_3944.
- activity_1628: Active connector repopulated downstream resource search_5268.
- activity_1647: Active connector repopulated downstream resource search_4054.
- activity_1911: Active connector repopulated downstream resource search_6432.
- activity_2100: Active connector repopulated downstream resource search_5659.
- activity_2314: Active connector repopulated downstream resource search_3120.
- activity_2358: Active connector repopulated downstream resource search_3627.

### Replica drift (first 30)

- evidence_05 account_994: missing from customer_data_primary
- evidence_10 account_6345: missing from customer_data_primary
- evidence_15 account_8023: missing from customer_data_primary
- evidence_20 account_4348: missing from customer_data_primary
- evidence_24 account_3150: missing from customer_data_primary
- evidence_24 account_9608: missing from customer_data_primary
- evidence_29 account_585: missing from customer_data_primary, customer_data_replica_eu_west
- evidence_33 account_6539: missing from customer_data_primary, customer_data_replica_ap_south
- evidence_33 account_8105: missing from customer_data_primary, customer_data_replica_ap_south
- evidence_38 account_5389: missing from customer_data_primary, customer_data_replica_eu_west
- evidence_43 account_3183: missing from customer_data_primary
- evidence_47 account_1035: missing from customer_data_primary
- evidence_54 account_005: missing from customer_profiles_primary
