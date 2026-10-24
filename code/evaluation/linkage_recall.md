# Linkage recall experiment (dev cases only)

Gathering uses only evidence with manifest `captured_at <= audit_at` and activity with `event_time <= audit_at`. Expected resources = every `resource_id` in the sample `steps_json` (findings, remediation and verification).

## Summary

| variant | recall | candidates | unexpected candidates | precision |
|---|---|---|---|---|
| no_batch | 49/70 (70%) | 49 | 0 | 100% |
| window | 64/70 (91%) | 275 | 211 | 23% |
| run | 65/70 (93%) | 104 | 39 | 62% |

## Per case

| case | status | exp | recall no_batch | cand no_batch | recall window | cand window | recall run | cand run | channels per expected resource (run variant) |
|---|---|---|---|---|---|---|---|---|---|
| case_01 | COMPLETE | 1 | 0/1 | 0 | 1/1 | 8 | 1/1 | 1 | account_001:batch_run |
| case_04 | COMPLETE | 2 | 0/2 | 0 | 1/2 | 8 | 1/2 | 1 | **account_004:MISS**; analytics_901:batch_run |
| case_05 | INCOMPLETE | 1 | 1/1 | 1 | 1/1 | 1 | 1/1 | 1 | account_005:direct,name |
| case_06 | INCOMPLETE | 2 | 2/2 | 2 | 2/2 | 31 | 2/2 | 2 | search_001:direct,name,resource_link; account_006:batch_run,direct,name,resource_link |
| case_07 | INCOMPLETE | 2 | 1/2 | 1 | 2/2 | 19 | 2/2 | 2 | support_001:direct,name; account_007:batch_run |
| case_09 | INCOMPLETE | 3 | 1/3 | 1 | 2/3 | 24 | 2/3 | 4 | backup_4978:direct; account_3960:batch_run; **audit_record_4902:MISS** |
| case_10 | INCOMPLETE | 2 | 1/2 | 1 | 1/2 | 12 | 2/2 | 2 | support_002:batch_run,direct; account_010:batch_run |
| case_11 | INCOMPLETE | 6 | 6/6 | 6 | 6/6 | 11 | 6/6 | 7 | search_052:direct; analytics_2034:direct; support_864:direct; data_flow_9522:direct; account_4248:batch_run,direct; audit_record_9394:direct |
| case_12 | INCOMPLETE | 3 | 3/3 | 3 | 3/3 | 3 | 3/3 | 3 | analytics_9914:direct; account_9797:batch_run,direct; audit_record_2945:direct |
| case_13 | INCOMPLETE | 4 | 4/4 | 4 | 4/4 | 7 | 4/4 | 4 | search_1308:direct; support_4218:direct; account_9335:direct; audit_record_4816:direct |
| case_15 | INCOMPLETE | 3 | 3/3 | 3 | 3/3 | 4 | 3/3 | 3 | data_flow_9846:direct; account_4745:batch_run,direct; audit_record_1596:direct |
| case_17 | INCOMPLETE | 3 | 3/3 | 3 | 3/3 | 8 | 3/3 | 3 | backup_3245:direct; account_4092:batch_run,direct; audit_record_1797:direct |
| case_21 | COMPLETE | 3 | 1/3 | 1 | 3/3 | 9 | 3/3 | 9 | account_6440:batch_run; audit_record_3737:direct; analytics_5335:batch_run |
| case_22 | INCOMPLETE | 4 | 3/4 | 3 | 4/4 | 6 | 4/4 | 5 | search_8911:direct,name; analytics_4440:direct; account_4990:batch_run; audit_record_3482:direct |
| case_23 | INCOMPLETE | 4 | 2/4 | 2 | 3/4 | 24 | 3/4 | 10 | export_7364:direct; search_898:direct; account_410:batch_run; **audit_record_4128:MISS** |
| case_24 | INCOMPLETE | 3 | 2/3 | 2 | 2/3 | 12 | 2/3 | 9 | account_5334:batch_run,direct; search_5577:direct; **audit_record_7095:MISS** |
| case_26 | COMPLETE | 6 | 4/6 | 4 | 6/6 | 30 | 6/6 | 11 | account_5684:batch_run; audit_record_5416:direct; support_4715:batch_run,direct; analytics_4566:batch_run; data_flow_6244:batch_run,direct; backup_3120:batch_run,direct |
| case_27 | COMPLETE | 6 | 4/6 | 4 | 6/6 | 9 | 6/6 | 9 | account_3866:batch_run; audit_record_3830:direct; support_4707:batch_run,direct; analytics_4574:batch_run; data_flow_2295:batch_run,direct; backup_5970:batch_run,direct |
| case_28 | COMPLETE | 6 | 4/6 | 4 | 6/6 | 24 | 6/6 | 9 | account_5482:batch_run; audit_record_9265:direct; support_2351:batch_run,direct; analytics_6077:batch_run; data_flow_2337:batch_run,direct; backup_838:batch_run,direct |
| case_29 | INCOMPLETE | 6 | 4/6 | 4 | 5/6 | 25 | 5/6 | 9 | search_1993:direct; analytics_5208:direct; support_5281:direct; data_flow_3827:direct; account_7012:batch_run; **audit_record_3954:MISS** |
| **total** | | 70 | 49/70 (70%) | 49 | 64/70 (91%) | 275 | 65/70 (93%) | 104 | |

## Misses (run variant)

| case | system | resource | diagnosis |
|---|---|---|---|
| case_04 | account_service | account_004 | appears in no participant file or activity row |
| case_09 | security_service | audit_record_4902 | only in evidence captured after audit_at (evidence_06) |
| case_23 | security_service | audit_record_4128 | only in evidence captured after audit_at (evidence_02) |
| case_24 | security_service | audit_record_7095 | only in evidence captured after audit_at (evidence_02) |
| case_29 | security_service | audit_record_3954 | only in evidence captured after audit_at (evidence_02) |

## Unexpected candidates by linking channel(s)

**no_batch:** 

**window:** batch_window 211

**run:** batch_run 39
