# SAMPLE_ANALYSIS — tracing all 32 sample answers back to the inputs

Method: for every row of `dataset/sample_output.csv` I resolved the requester through
`user_identities.csv` and `user_audit_log.csv`. I then grepped every evidence file (text, SQL,
logs, JSON, `pdftotext` of PDFs, screenshots read visually) for each identifier and each answer
`resource_id`, and listed `system_activity.csv` rows for those resources. "VIS" means
`captured_at ≤ audit_at`; "late" means captured after `audit_at`. Companion notes:
[DATA_NOTES.md](DATA_NOTES.md).

Notation: *F* = FINDING, *R* = REMEDIATION, *V* = VERIFICATION; `ev_NN` = `evidence_NN`.
"Act" = activity row by `deletion_worker` unless noted.

---

## 1. Summary table

| case | status | subject (match) | reported | what drives the status | cited |
|---|---|---|---|---|---|
| 01 | COMPLETE | user_01 (cur) | COMPLETE | account absent from failover snapshot | ev_54 |
| 02 | COMPLETE | user_02 (cur) | PARTIALLY | same; activity was PARTIAL, but the state is absent | ev_54 |
| 03 | COMPLETE | user_43 (cur) | COMPLETE | 404 account, audit kept, analytics nulled | ev_94 |
| 04 | COMPLETE | user_04 (cur) | COMPLETE | account absent, analytics nulled | ev_54 + ev_56 |
| 05 | INCOMPLETE | user_05 (cur) | COMPLETE | account still in **failover replica** | ev_54 |
| 06 | INCOMPLETE | user_06 (cur) | PARTIALLY | search doc still in primary index | ev_57 |
| 07 | INCOMPLETE | user_07 (cur) | COMPLETE | support attachment PDF still exists | ev_58 |
| 08 | INCOMPLETE | user_08 (cur) | PARTIALLY | user export still downloadable | ev_59 |
| 09 | INCOMPLETE | user_61 (cur) | NOT_STARTED | backup `restore_allowed=1` | ev_93 |
| 10 | INCOMPLETE | user_10 (**hist**) | COMPLETE | legacy support contact record under old email | ev_61 |
| 11 | INCOMPLETE | user_32 (cur) | COMPLETE | search + analytics + support + active flow | ev_03 |
| 12 | INCOMPLETE | user_12 (cur, old hash used) | COMPLETE | analytics contact record keyed by **old email_hash** | ev_01 |
| 13 | INCOMPLETE | user_33 (cur) | NOT_STARTED | search + support attachment | ev_03 |
| 14 | INCOMPLETE | user_37 (cur) | COMPLETE | backup restorable | ev_03 |
| 15 | INCOMPLETE | user_15 (cur) | COMPLETE | connector still enabled | ev_01 |
| 16 | INCOMPLETE | user_53 (cur) | NOT_STARTED | account in **read replica only** + search doc | ev_05 |
| 17 | INCOMPLETE | user_17 (cur) | NOT_STARTED | backup eligible for restore | ev_01 |
| 18 | INCOMPLETE | user_50 (cur) | COMPLETE | search + analytics + support + active flow | ev_94 |
| 19 | INCOMPLETE | user_19 (cur) | PARTIALLY | search + support message with PII | ev_01 |
| 20 | INCOMPLETE | user_48 (cur) | NOT_STARTED | search + active flow | ev_94 |
| 21 | COMPLETE | user_21 (cur) | COMPLETE | 404 account, audit kept, analytics nulled | ev_92 |
| 22 | INCOMPLETE | user_51 (cur) | COMPLETE | search + identifiable analytics | ev_05 |
| 23 | INCOMPLETE | user_23 (cur) | NOT_STARTED | export + search | ev_92 |
| 24 | INCOMPLETE | user_24 (cur) | COMPLETE | account **still 200** + search | ev_92 |
| 25 | COMPLETE | user_25 (cur) | NOT_STARTED | 404 account, audit kept, analytics nulled | ev_92 |
| 26 | COMPLETE | user_26 (cur) | NOT_STARTED | all six treatments done (older report superseded) | ev_92 |
| 27 | COMPLETE | user_27 (cur) | COMPLETE | all six treatments done | ev_92 |
| 28 | COMPLETE | user_28 (cur) | NOT_STARTED | all six done; `.jr` search decoy ignored | ev_92 |
| 29 | INCOMPLETE | user_29 (**hist**) | NOT_STARTED | search + analytics + support + flow, all under old email | ev_92 |
| 30 | INCOMPLETE | user_30 (**hist**) | COMPLETE | same pattern as 29 | ev_92 |
| 31 | COMPLETE | user_541 (cur) | COMPLETE | tombstone fully nulled, empty account snapshot | ev_51 |
| 32 | INCOMPLETE | user_542 (cur) | PARTIALLY | tombstone still holds name/email/phone/hash | ev_51 |

`reported_status` is uncorrelated with the true status. COMPLETE claims turn out INCOMPLETE (05, 07, 10–12, 14, 15, 18, 22, 24, 30). NOT_STARTED claims turn out COMPLETE (25, 26, 28).

---

## 2. Per-case traces

### case_01: COMPLETE
- **Identifiers:** `amina.elmasri@gmail.com` → user_01 (current, no history).
- **Found:** account_001 has no row in `customer_accounts_failover_replica_20260902.sql` (ev_54), in either the primary or the failover table. Act 3342 DELETE account_001 SUCCESS (claim).
- **Rule:** USER_PROFILE → DELETE.
- **Why COMPLETE:** the only relevant resource is absent in the latest visible account snapshot.
- **Cited ev_54:** latest account-service capture before audit (09-02T23:00 ≤ 09-03T11:30). Absence from a snapshot is used as proof of absence.
- **Note:** account_001 is *never written* in any evidence file. The resource ID comes only from the activity row, and the link to user_01 comes only from the numbering convention (`account_0NN` ↔ `user_0NN` in the hand-made fixtures).

### case_02: COMPLETE
- **Identifiers:** `mateo.rios@outlook.com` → user_02 (current).
- **Found:** act 3376 DELETE account_002 **PARTIAL** ("could not confirm the final state"). ev_54 has no account_002. A decoy `Mateo Rios Gil`/account_9116 is still in ev_54 primary and must not be matched.
- **Rule:** USER_PROFILE → DELETE.
- **Why COMPLETE:** evidence shows the account absent, which overrides the PARTIAL claim and the Spanish "I got an error" story.
- **Cited ev_54:** same reason as case_01.

### case_03: COMPLETE
- **Identifiers:** `l.fontaine46@yahoo.com` → user_43 (current). French text.
- **Found (all in ev_94, `resource_lookup_trace_20260926`):**
  - `profile_lookup` with lookup_key=email gives account_7566 rows=0/404;
  - `audit_record_read` lookup user_43 gives audit_record_2016 (IDENTITY_CHANGE_RECORDED, retain → 2034);
  - analytics_9406 has `lookup_key=null` and all identifiers null.
- **Activity:** 3851 DELETE account_7566, 3852 ANONYMIZE analytics_9406 SUCCESS.
- **Rules:** USER_PROFILE→DELETE (absent ✓), SECURITY_AUDIT_LOG→RETAIN (present ✓), ANALYTICS_PROFILE→ANONYMIZE (nulled ✓).
- **Cited ev_94:** captured 09-26T10:38 ≤ audit 09-26T11:00; latest; shows all three states.
- **Uncertain:** nothing in ev_94 ties analytics_9406 to user_43 (lookup_key=null). The only link is activity 3852, which follows account_7566's delete by 24 min.

### case_04: COMPLETE
- **Identifiers:** `thandiwe.n@gmail.com` → user_04 (current).
- **Found:** account_004 is absent from ev_54. analytics_901 in `customer_analytics_report_20260907.json` (ev_56) has user_id/email/phone/hash all null. The same file holds decoy analytics_824 (`dahl.freja@…`, user_824, identifiable).
- **Rules:** USER_PROFILE→DELETE; ANALYTICS_PROFILE→ANONYMIZE ("keep anonymous statistics" in the request).
- **Why COMPLETE:** both treatments are in place.
- **Cited:** ev_54 for the account and ev_56 for analytics. This is the **only sample citing two files**, because no single file covers both systems.
- **Uncertain:** account_004 appears **nowhere** in participant data, not even in activity. analytics_901 links to the user only through act 3463 ("ANONYMIZE … analytics_901 SUCCESS"), which has no user on it. These hand-made fixtures were not generated from discoverable links.

### case_05: INCOMPLETE
- **Identifiers:** `leila.haddad@yahoo.com` → user_05.
- **Found:** ev_54 `customer_profiles_failover_replica` still has `account_005 | user_05 | Leila Haddad | … | ACTIVE`. The primary table has no such row. Act 3482 DELETE SUCCESS, then act 3483 SYNC to `account_failover_replica` (the replication checkpoint that kept the copy).
- **Rule:** USER_PROFILE → DELETE.
- **Steps:** F REMAINING_RECORD account_005 → R DELETE → V ABSENT.
- **Why INCOMPLETE:** a replica copy counts as a remaining record even though primary is clean.
- **Cited ev_54:** the only file showing the copy.

### case_06: INCOMPLETE
- **Identifiers:** `jonas.berg@outlook.com` → user_06.
- **Found:** `customer_search_primary_20260910.sql` (ev_57) holds `search_001 | Jonas Berg | jonas.berg@… | source_resource_id=account_006`. Decoys in the same table: search_8814 Jonas Berglund, search_4203 Joana Berg. Act 3546 DELETE search_001 FAILED; act 3545 DELETE account_006 SUCCESS.
- **Rule:** SEARCH_INDEX_ENTRY → DELETE.
- **Steps:** F search_001 → R DELETE → V account_006 ABSENT, search_001 ABSENT.
- **Cited ev_57:** the only file with the search doc (captured 09-10T08:45 ≤ 09-10T09:15).
- **Note:** account_006's ID is known from `source_resource_id` and the activity row. No file shows the account itself, but V still checks it.

### case_07: INCOMPLETE
- **Identifiers:** `priya.raman@gmail.com` → user_07.
- **Found:** `delivery_correction_attachment.pdf` (ev_58, captured 09-12T07:30 ≤ 08:00) is "Support reference CS-4827 / support_001" and contains Priya Raman's name, email and phone. The request explicitly names "the identity document I sent to support".
- **Rule:** SUPPORT_ATTACHMENT (belongs only to subject) → DELETE.
- **Steps:** F REMAINING_RECORD support_001 → R DELETE → V account_007 ABSENT, support_001 ABSENT.
- **Cited ev_58:** the PDF itself.
- **Note:** act 4007 (LINK) and the object-storage screenshot ev_46 describe a *different* attachment (`object_19`, delivery_confirmation_19.pdf, 02-13). That belongs to the eval story case_61 for the same user (audit 02-14) and is **not** part of this answer. No support_001 activity exists.

### case_08: INCOMPLETE
- **Identifiers:** `gabriel.costa@proton.me` → user_08.
- **Found:** `customer_export_jobs_20260914.json` (ev_59) holds export_001, owner gabriel.costa@…, `download_available: true`, expires 10-04. Decoy export_843 (aoi-tanaka, expired). Act 3555 DELETE export_001 FAILED.
- **Rule:** USER_EXPORT → DELETE.
- **Cited ev_59.**
- **Note:** user_08 also appears in `legal_retention_register_20260529.pdf` (ev_27: ORD-20264118, card dispute, retain to 2033). The answer has **no legal-hold step**.

### case_09: INCOMPLETE
- **Identifiers:** `k.appiah88@gmail.com` → user_61.
- **Found:** `customer_data_inventory_20260916.sql` (ev_93, 09-16T08:37 ≤ 09:00) row `backup_restore_entries('backup_4978', NULL, 'k.appiah88@gmail.com', …, restore_allowed='1')`. Act 3709 CHECK_RESTORE_ACCESS "can still be restored".
- **Rule:** BACKUP_ARCHIVE → RETAIN, and restore must be blocked.
- **Steps:** F RESTORE_RISK → R BLOCK_RESTORE → V account_3960 ABSENT, audit_record_4902 PRESENT, backup_4978 RESTORE_BLOCKED.
- **Cited ev_93.**
- **Important:** account_3960 and audit_record_4902 occur **only** in `structured_service_events_eu_west_20260918.log` (ev_06). That file was captured 09-18, after audit 09-16, although its relevant lines are timestamped 09-16T08:52–08:57, before audit. Act 3703 DELETE account_3960 FAILED has no user link. So the answer uses resource IDs from a file that may not be cited. It looks like *line-level* timestamps, or a ground truth that ignores visibility, decide the verification resources.

### case_10: INCOMPLETE
- **Identifiers:** `nadia.ionescu@yahoo.com` is a **historical** email. audit_121: nadia.ionescu → nadia.petrescu (2025-12); audit_238: → n.petrescu (2026-04). Current is user_10 n.petrescu@yahoo.com. This is a two-step chain.
- **Found:** `support_replica.log` (ev_61): trace tr-rp-002, `contact_query_started lookup_key=nadia.ionescu@yahoo.com source=legacy_contact_index` → `completed resource_id=support_002 rows=1` → `deletion_marker_check marker_found=false`. Decoys: support_4104 `customer4104@yahoo.com`, support_5914 `nadia.popescu@yahoo.com`. Act 3596 DELETE support_002 **SUCCESS** (claim contradicted).
- **Rule:** DELETE. Either SUPPORT_ATTACHMENT or SECONDARY_IDENTIFIER (legacy contact index); not REDACT.
- **Steps:** F REMAINING_RECORD support_002 → R DELETE → V account_010 ABSENT, support_002 ABSENT.
- **Cited ev_61.** `system_id` is `support_platform` even though the log's service is `support_replica`.

### case_11: INCOMPLETE
- **Identifiers:** `novak.tomasz@icloud.com` → user_32.
- **Found:** `customer_account_handover_20261001.txt` (ev_03, captured exactly at audit 10-01T12:00). It lists:
  - account_4248 USER_PROFILE NOT_FOUND
  - search_052 SEARCH_INDEX_ENTRY FOUND
  - analytics_2034 ANALYTICS_PROFILE IDENTIFIABLE
  - support_864 SUPPORT_MESSAGE IDENTIFIABLE
  - data_flow_9522 DATA_FLOW ACTIVE
  - audit_record_9394 SECURITY_AUDIT_LOG FOUND (user_32)

  Activity: 3814 DELETE search FAILED, 3815 REDACT support FAILED, 3816 ANONYMIZE analytics FAILED, 3817 CHECK_DATA_FLOW "remains enabled".
- **Rules:** search→DELETE (REMAINING_RECORD); analytics→ANONYMIZE and support message→REDACT (both EXPOSED_PII); data flow→DISABLE.
- **Steps (14):** F search, analytics, support, flow → R DISABLE flow, DELETE search, REDACT support, ANONYMIZE analytics → V account ABSENT, search ABSENT, audit PRESENT, support REDACTED, analytics ANONYMIZED, flow DISABLED.
- **Cited ev_03:** only file, captured at exactly audit_at, which shows the cut-off is inclusive.

### case_12: INCOMPLETE
- **Identifiers:** `minjun.kim@gmail.com` → user_12 (current). History: audit_282 email minjun.kim90@gmail.com; audit_283 old hash `44a65154…`.
- **Found in `service_events_ap_south_20260915.log` (ev_01):**
  - account lookup minjun.kim@gmail.com joined by trace tr-8001359 to account_9797 404;
  - audit_record_2945 `correlation_key="user_12"`;
  - analytics_9914 `event=contact_lookup_completed rows=1 email_opt_in=true correlation_key="44a65154…"`, the **old email hash**.

  Act 3435 **DELETE_RECORD** analytics_9914 FAILED.
- **Rule applied:** analytics_9914 is **DELETEd** (REMAINING_RECORD → DELETE → V ABSENT), not anonymized as rule 07 would suggest.
- **Cited ev_01.**
- **Uncertain:** why DELETE? There are two candidate signals. (a) The record is a contact/opt-in record keyed by an identifier, i.e. SECONDARY_IDENTIFIER or MARKETING-like. (b) The deletion worker's own event_type for the resource was DELETE_RECORD (see rule R8). user_12 is also on the legal register (ORD-20264207), which the answer ignores.

### case_13: INCOMPLETE
- **Identifiers:** `noor-khalil@proton.me` → user_33.
- **Found:** handover ev_03 lists account_9335 NOT_FOUND, search_1308 FOUND, support_4218 **SUPPORT_ATTACHMENT** FOUND, and audit_record_4816 FOUND (user_33). Activity 3739/3752/3756 DELETE … FAILED.
- **Rules:** search→DELETE; attachment→DELETE.
- **Steps:** F search, support (both REMAINING_RECORD, sorted search < support) → R DELETE ×2 → V account, search, support ABSENT; audit PRESENT.
- **Cited ev_03.**

### case_14: INCOMPLETE
- **Identifiers:** `y.tanaka32@gmail.com` → user_37. Japanese text.
- **Found:** ev_03 lists account_3677 NOT_FOUND, backup_3870 BACKUP_ARCHIVE **ACTIVE** (email), and audit_record_987 FOUND. Act 3690 CHECK_RESTORE_ACCESS "can still be restored".
- **Rule:** BACKUP_ARCHIVE → RETAIN + BLOCK_RESTORE.
- **Cited ev_03.**

### case_15: INCOMPLETE
- **Identifiers:** `lucia-torres@gmail.com` → user_15 (one phone change, unused).
- **Found in ev_01:**
  - account_4745 404 (`correlation_key="lucia-torres@…"`);
  - trace tr-9750867 `connector_config_read_started subject_key=lucia-torres@…` → `connector_heartbeat data_flow_9846 enabled=true scheduler_registered=true`;
  - audit_record_1596 via subject_ref user_15 (trace join).

  Act 3386 CHECK_DATA_FLOW "remains enabled". The screenshot `connector_management_console_20260807` (ev_14, VIS) also shows data_flow_9846 → lucia-torres, Continuous.
- **Rule:** active flow → DISABLE.
- **Cited ev_01:** the latest visible file, chosen over the older screenshot.

### case_16: INCOMPLETE
- **Identifiers:** `diya.k4436@outlook.com` → user_53.
- **Found in `customer_data_read_replica_20260921.sql` (ev_05, 09-21T10:00 = audit):**
  - `customer_data_primary` has search_1007 and audit_record_9528 but **no** account;
  - `customer_data_read_replica` has **account_994** (Diya Kapoor, ACTIVE), search_1007 and audit_record_9528.

  Act 3571 DELETE account_994 FAILED; 3577 SYNC replication; 3580 DELETE search_1007 **SUCCESS** (claim contradicted).
- **Rules:** USER_PROFILE→DELETE (replica copy); SEARCH→DELETE.
- **Steps:** F account_994 + search_1007 REMAINING → R DELETE ×2 → V account, search ABSENT, audit PRESENT.
- **Cited ev_05.** Replication drift is a finding.

### case_17: INCOMPLETE
- **Identifiers:** `leonie.g9352@outlook.com` → user_17.
- **Found in ev_01:** account_4092 404 (trace join); audit_record_1797 (`correlation_key=user_17`); trace tr-8210651 `restore_policy_check_started subject_key=leonie…` → `restore_candidate_evaluated backup_3245 eligible=true deletion_block=false`. Act 3385 CHECK_RESTORE_ACCESS "can still be restored".
- **Rule:** BACKUP_ARCHIVE → BLOCK_RESTORE.
- **Cited ev_01.**
- **Not in answer:**
  - account_6523, a second account looked up for the same email in `resource_lookup_trace_20260310` (ev_67, 404);
  - tombstone_17 (act 4011 LINK, screenshot ev_42 "Retained reference user_17", no PII);
  - legal register ORD-20264319.

  These belong to eval case_46 (same email, audit 03-11). **Uncertain** whether a solver should still include them for case_17 or whether ground truth is scoped per "story".

### case_18: INCOMPLETE
- **Identifiers:** `mori.ren@gmail.com` → user_50.
- **Found in ev_94 (`resource_lookup_trace_20260926`, ≤ audit 09-26T11:00):**
  - support_5569 `requester_email=mori.ren@…`;
  - analytics_037 `email=mori.ren@…`;
  - data_flow_4890 `enabled=true analytics_identity_bridge`;
  - search_4013 `result=hit`.

  Activity: REDACT support FAILED, DELETE search FAILED, ANONYMIZE analytics FAILED, CHECK_DATA_FLOW enabled.
- **Steps:** 14 steps, the same shape as case_11.
- **Cited ev_94.**
- **Important:** the V steps use account_5521 (appears **only** in act 3732 "DELETE account_5521 SUCCESS", no user link) and audit_record_6645 (appears **nowhere** in participant data). Neither can be derived from the dataset.

### case_19: INCOMPLETE
- **Identifiers:** `e.varga90@yahoo.com` → user_19.
- **Found in ev_01:** trace tr-8896806 `message_scan_started subject_key=e.varga90…` → `message_pii_scan support_9553 matched_value=e.varga90@… detector=direct_identifier`; search_6083 hit (correlation_key email); audit_record_6793 (subject_ref user_19); account_9102 404. Act 3329 REDACT support FAILED, 3332 DELETE search FAILED.
- **Rules:** SUPPORT_MESSAGE with shared context → **REDACT** (the request says "messages also relate to another customer, so the conversation itself may need to stay"); search → DELETE.
- **Steps:** F search REMAINING, support EXPOSED_PII → R DELETE search, REDACT support → V account ABSENT, search ABSENT, audit PRESENT, support REDACTED.
- **Cited ev_01.**

### case_20: INCOMPLETE
- **Identifiers:** `akosua.gyasi@gmail.com` → user_48.
- **Found in ev_94:** search_4262 hit; data_flow_1770 `enabled=true customer_events_connector customer_email=akosua…`. Act 3745 CHECK_DATA_FLOW, 3750 DELETE search FAILED.
- **Cited ev_94.**
- **Important:** account_5037 appears only in act 3734 (no user link), and audit_record_5569 appears **nowhere**. Same non-derivability as case_18.

### case_21: COMPLETE
- **Identifiers:** `amara-okeke@proton.me` → user_21.
- **Found in `service_resource_export_20260915_114455.json` (ev_92, 09-15T11:44 ≤ 12:00):** account_6440 status 404 with profile null; analytics_5335 all identifiers null; audit_record_3737 subject_reference user_21 (retain → 2034).
- **Rules:** DELETE ✓, ANONYMIZE ✓, RETAIN ✓.
- **Cited ev_92.**
- **Uncertain:** neither account_6440 nor analytics_5335 carries any identifier in any file. They are linkable only via activity 3661 (DELETE account_6440) and 3665 (ANONYMIZE analytics_5335), which interleave with other cases' rows. user_21 is also on the legal register (no step).

### case_22: INCOMPLETE
- **Identifiers:** `seoyeon-han@gmail.com` → user_51.
- **Found in ev_05 (read-replica dump, primary and replica identical for these rows):** search_8911 (Seo-yeon Han, user_51); analytics_4440 with user_id, email, phone, hash all present; audit_record_3482 (user_51). Act 3578 ANONYMIZE analytics SUCCESS (contradicted), 3581 DELETE search SUCCESS (contradicted).
- **Steps:** F search REMAINING, analytics EXPOSED_PII → R DELETE, ANONYMIZE → V account_4990 ABSENT, search ABSENT, audit PRESENT, analytics ANONYMIZED.
- **Cited ev_05.**
- **Note:** account_4990 is only in act 3573. In ev_05 the account is absent from both tables, so no finding.

### case_23: INCOMPLETE
- **Identifiers:** `thandiwe.m6866@zoho.com` → user_23.
- **Found in ev_92:** export_7364 (owner_email = request email, object_key present) and search query → search_898 hit. Act 3667 DELETE export FAILED, 3671 DELETE search FAILED.
- **Steps:** F export, search (sorted export < search) → R DELETE ×2 → V account_410 ABSENT, export ABSENT, search ABSENT, audit_record_4128 PRESENT.
- **Cited ev_92.**
- **Note:** audit_record_4128 → user_23 appears only in late ev_02 (bundle 09-17). account_410 appears only in act 3662.

### case_24: INCOMPLETE
- **Identifiers:** `keiko.nakamura@gmail.com` → user_24.
- **Found in ev_92:** account_5334 **status_code 200** with profile.email = keiko…; search_5577 hit. Act 3599 DELETE account SUCCESS (contradicted), 3608 SYNC (replication).
- **Steps:** F account + search REMAINING → R DELETE ×2 → V account, search ABSENT; audit_record_7095 PRESENT.
- **Cited ev_92.**
- **Note:** audit_record_7095 appears only in late ev_02. user_24 is on the legal register (no step).

### case_25: COMPLETE
- **Identifiers:** `s.yoon04@gmail.com` → user_25.
- **Found in ev_92:** account_3136 404; analytics_1895 nulled; audit_record_451 (user_25, ACCOUNT_DELETION_RECORDED, retain → 2031). Act 3597 DELETE account **FAILED**, but the state shows absent, so the claim is wrong in the other direction.
- **Cited ev_92.**
- **Uncertain:** account and analytics are identity-free, so the link is via activity only (as in case_21).

### case_26: COMPLETE
- **Identifiers:** `almeida.mariana@gmail.com` → user_26.
- **Found in ev_92:**
  - account_5684 404;
  - analytics_4566 nulled;
  - backup_3120 `customer_reference=user_26, restore_allowed=false`;
  - data_flow_6244 `customer_reference=user_26, enabled=false, stopped`;
  - support_4715 `customer_reference=user_26`, requester fields null, generic excerpt;
  - audit_record_5416 (user_26).

  Activity: 3603 ANONYMIZE, 3605 REDACT, 3606 DISABLE_DATA_FLOW, 3607 BLOCK_RESTORE, all SUCCESS.
- **Steps:** V ×6 in table order: ABSENT, PRESENT, REDACTED, ANONYMIZED, DISABLED, BLOCKED.
- **Why COMPLETE despite the "old report":** late bundle ev_02 (09-17) contains a snapshot captured 09-09 with account_5684 ACTIVE. That is older state, superseded by the 09-15 404 in ev_92 (and ev_02 is not visible anyway).
- **Cited ev_92.**
- **Rules shown:** a support record that keeps `customer_reference` but drops requester PII counts as REDACTED. A backup with restore_allowed=false counts as BLOCKED, and a flow with enabled=false counts as DISABLED.

### case_27: COMPLETE
- **Identifiers:** `ananya-nair@gmail.com` → user_27.
- **Found in ev_92:** account_3866 404, analytics_4574 nulled, backup_5970, data_flow_2295, support_4707 and audit_record_3830 (all user_27, compliant).
- **Cited ev_92.**
- **Uncertain:** analytics_4574 vs analytics_6077 (case_28). Both were anonymized 44 s apart (act 3533/3534), and their batch activities are interleaved. Nothing in the evidence decides which analytics record belongs to which user.

### case_28: COMPLETE
- **Identifiers:** `smartinez6461@gmail.com` → user_28.
- **Found in ev_92:** the same six-resource compliant pattern (account_5482 404, analytics_6077, backup_838, data_flow_2337, support_2351, audit_record_9265).
- **Cited ev_92.**
- **Decoy:** search_8168 `smartinez6461.jr@gmail.com` (late ev_02; request text warns "belongs to someone else") must **not** be matched; exact email match only.

### case_29: INCOMPLETE
- **Identifiers:** `noah.f438085@outlook.com` is **historical**. audit_284 → current noah.f4380@outlook.com (user_29).
- **Found in ev_92, all keyed by the *old* email:** search_1993 (query/email old), analytics_5208 (email old), support_5281 (requester_email old), data_flow_3827 (`customer_email` old, enabled, delivered). Activity: 3541 CHECK_DATA_FLOW, 3542 DELETE FAILED, 3543 REDACT FAILED, 3544 ANONYMIZE FAILED.
- **Steps:** 14 steps, same shape as case_11.
- **Cited ev_92.**
- **Note:** in late ev_02 analytics_5208 shows the *current* email. account_7012 appears only in act 3532. audit_record_3954 appears only in late ev_02. user_29 is on the legal register (no step).

### case_30: INCOMPLETE
- **Identifiers:** `camille.bernard86@outlook.com` is **historical** → user_30 (current camille.bernard@outlook.com).
- **Found in ev_92:** search_9359, analytics_3697, support_6477, data_flow_8524, all under the old email.
- **Steps:** 14 steps, same shape as case_11.
- **Cited ev_92.**
- **Note:** account_4163 appears only in act 3465; audit_record_1707 only in late ev_02.

### case_31: COMPLETE
- **Identifiers:** `a.eze08@gmail.com` → user_541.
- **Found in `customer_data_snapshot_bundle_20260915.json` (ev_51, = audit 09-15T12:00):** account_service/customer_profiles `records: []`; identity_service tombstone_440 `subject_reference=user_541`, name/email/phone/hash **null**, DELETED; audit_record_6909 (user_541). Act 3687 REDACT tombstone_440 SUCCESS.
- **Rules:** ACCOUNT_TOMBSTONE → REDACT (only the stable ref kept) ✓.
- **Steps:** V account ABSENT, audit PRESENT, tombstone REDACTED.
- **Cited ev_51.**
- **Note:** account_4857 appears only in act 3682. The empty account snapshot is cited as proof of absence.

### case_32: INCOMPLETE
- **Identifiers:** `addo.kofi@gmail.com` → user_542.
- **Found in ev_51:** tombstone_9178 `subject_reference=user_542` **still holding name, email, phone and email_hash**. Account snapshot empty. audit_record_7068 present. Act 3692 REDACT tombstone FAILED; account_8858 act 3685 PARTIAL.
- **Steps:** F EXPOSED_PII tombstone → R REDACT → V account ABSENT, audit PRESENT, tombstone REDACTED.
- **Cited ev_51.**

---

## 3. Resource discoverability check

For every non-remediation step I checked where its `resource_id` appears.

| class | cases → resources |
|---|---|
| In a **visible** evidence file, linked to the subject | all findings, plus most verification resources |
| Only in **activity** (no evidence) | account_001/002 (01, 02), account_007/008/010 (07, 08, 10), account_5521 (18), account_5037 (20), account_4990 (22), account_410 (23), account_7012 (29), account_4163 (30), account_4857 (31), account_8858 (32) |
| Only in a **late** file (captured after audit_at) | account_3960, audit_record_4902 (09, ev_06); audit_record_4128 / 7095 / 3954 / 1707 (23 / 24 / 29 / 30, ev_02) |
| **Nowhere** in participant data | account_004 (04), audit_record_6645 (18), audit_record_5569 (20) |
| In visible evidence but **identity-free** (link only via activity) | account_6440, analytics_5335 (21); account_3136, analytics_1895 (25); account_5684 (26, though identifiable in late ev_02); analytics_4566 / 4574 / 6077 (26–28); analytics_9406 (03); analytics_901 (04) |

**Every finding cites a visible file and is fully derivable.** Verification-only resources (the account, the audit record and anonymized analytics) frequently are not derivable. Findings are reliably achievable, but some verification steps will be unattainable or need heuristics.

---

## 4. Inferred rules and conventions

Confidence: **[C]** confirmed in all applicable samples · **[L]** likely, with minor exceptions or few examples · **[U]** uncertain or guessed.

### Identity
- **R1 [C]** The request email matches case-insensitively against current emails or any `old_value` in `user_audit_log` (chains followed: case_10 is two hops). Exactly one user results.
- **R2 [C]** Search evidence with all identifier forms: current and old emails, current and old `email_hash` (case_12), `user_id` in `customer_reference`/`subject_reference`/`correlation_key`/`subject_selector`.
- **R3 [C]** Exact match only. `.jr` addresses, lookalike names and other users with the same first name are decoys (cases 02, 04, 06, 10, 28).
- **R4 [C]** In logs, join `*_started` (carries the email/user key) to `*_completed`/`*_scan`/`*_evaluated` (carries the `resource_id`) by trace id.

### Time
- **R5 [C]** A file is citable iff `captured_at ≤ audit_at` (inclusive: cases 11, 13, 14, 16 and 31 cite files captured exactly at audit_at).
- **R6 [L]** Use the latest visible state per resource. Older ACTIVE snapshots are superseded (case_26), and a later 404 overrides earlier presence.
- **R7 [U]** Ground truth seems to know resource identities from files captured after audit_at (case_09 ev_06; ev_02 for audit records). Whether a solver may *use, but not cite* late files for linking is unclear.

### Claims vs. state
- **R8 [C]** `reported_status` and activity `reported_result` never decide the status. SUCCESS claims are contradicted (cases 10, 16, 22, 24) and FAILED claims are overridden (cases 25, 26, 28).
- **R9 [L]** The deletion worker's *event_type* for a resource matches the remediation operation in every sample:

  | activity event_type | remediation |
  |---|---|
  | DELETE_RECORD | DELETE |
  | REDACT_RECORD | REDACT |
  | ANONYMIZE_RECORD | ANONYMIZE |
  | CHECK_DATA_FLOW "remains enabled" | DISABLE_DATA_FLOW |
  | CHECK_RESTORE_ACCESS "can still be restored" | BLOCK_RESTORE |

  For COMPLETE cases, the SUCCESS rows DISABLE_DATA_FLOW / BLOCK_RESTORE / REDACT / ANONYMIZE match the verification ops. The event type tells you the *intended treatment*; only its result is untrustworthy. This explains case_12 (analytics → DELETE). It is a strong hint but should not override the policy table.

### Policy → operation
- **R10 [C]** USER_PROFILE, SEARCH_INDEX_ENTRY, USER_EXPORT and SUPPORT_ATTACHMENT are DELETE categories. When the record exists: FINDING `REPORT_REMAINING_RECORD` → `DELETE_RECORD` → `VERIFY_RECORD_ABSENT`.
- **R11 [C]** ANALYTICS_PROFILE: when identifiers are present, `REPORT_EXPOSED_PII` → `ANONYMIZE_RECORD` → `VERIFY_RECORD_ANONYMIZED`. When all are null, `VERIFY_RECORD_ANONYMIZED` only. **Exception [U]**: case_12, where a contact/opt-in analytics record keyed by an old hash is DELETEd.
- **R12 [L]** SUPPORT_MESSAGE with requester PII: `REPORT_EXPOSED_PII` → `REDACT_RECORD` → `VERIFY_RECORD_REDACTED`. If it is an attachment or a legacy contact-index entry, it is a DELETE category (cases 07, 10, 13). Distinguishing signals: `record_type` SUPPORT_ATTACHMENT, `attachment_pii_scan`, `ticket_attachments`, PDF attachment, `legacy_contact_index` → DELETE; `message_pii_scan`, `support_conversations`, `SUPPORT_MESSAGE` → REDACT. **[U]** for case_10.
- **R13 [C]** Data flow enabled/ACTIVE/heartbeat: `REPORT_ACTIVE_DATA_FLOW` → `DISABLE_DATA_FLOW` → `VERIFY_DATA_FLOW_DISABLED`. When it is already disabled, only the verification step.
- **R14 [C]** Backup restore_allowed/eligible/ACTIVE: `REPORT_RESTORE_RISK` → `BLOCK_RESTORE` → `VERIFY_RESTORE_BLOCKED`. When it is already blocked, only the verification step. The backup is never deleted.
- **R15 [C]** Tombstone: any non-null name/email/phone/email_hash gives `REPORT_EXPOSED_PII` → `REDACT_RECORD` → `VERIFY_RECORD_REDACTED`. All null gives `VERIFY_RECORD_REDACTED` only. `system_id=identity_service`.
- **R16 [C]** Security audit record: never a finding or remediation. It always gets `VERIFY_RECORD_PRESENT` when known (24/32, including INCOMPLETE cases with empty evidence_id).
- **R17 [L]** Legal hold register rows (PDF, no resource_id) produce **no steps**. user_08/12/17/21/24/29 are listed but never appear in answers. Whether `legal_hold_*` resources from SQL would produce `VERIFY_RECORD_PRESENT` is **[U]**, since no sample covers it.

### Status
- **R18 [C]** INCOMPLETE iff at least one finding exists (remaining record, exposed PII, active flow or restore risk). Otherwise COMPLETE. No sample is NOT_APPLICABLE. All sample requests authorise deletion, in English, Spanish, French or Japanese.

### Steps and serialization
- **R19 [C]** Phase order is F → R → V. Within a phase, sort by operation order as in the spec table, then `system_id` asc, then `resource_id` asc. This was checked on every sample. Examples: `export < search`, `analytics < support` within EXPOSED_PII, and `DISABLE` before `DELETE` in remediation.
- **R20 [C]** VERIFICATION always contains `account_service <account> VERIFY_RECORD_ABSENT`, even when no account finding exists. It contains one verification per finding resource plus the retained audit record. In COMPLETE cases it covers every known subject resource in its compliant state.
- **R21 [C]** INCOMPLETE: `evidence_id` is set on FINDING steps only, and `""` on all REMEDIATION and VERIFICATION steps. COMPLETE: VERIFICATION only, each step citing evidence.
- **R22 [L]** Citation choice: one file per case, the **latest visible file** that shows the resource state (31/32). case_04 is the exception: two files, because no single file covered both systems. Older visible files showing the same thing (e.g. ev_14 in case_15) are not cited.
- **R23 [L]** Absence is proven by a snapshot that covers the system and lacks the record: ev_54 for account_001/002/004, ev_51's empty `records: []`. A 404 lookup also proves absence (ev_01, ev_92, ev_94). The tie-break between equally valid absence proofs is **[U]**: case_04 uses ev_54 although the account-only inventory ev_90 (09-05) is later.
- **R24 [C]** Output system_ids are canonical. The `support_replica` log maps to `support_platform` (case_10). The values seen are account_service, search_service, analytics_platform, support_platform, export_service, integration_service, backup_service, security_service and identity_service.
- **R25 [L]** The same subject may have several cases (eval reuses sample users 03, 07, 09, 11, 13, 14, 16–18, 20, 22). Story-specific fixtures (screenshots, LINK activity rows, PDFs) belong to the eval case whose `audit_at` sits just after the fixture. They are *not* folded into the sample answers (case_07 ignores object_19; case_17 ignores account_6523 and tombstone_17). **[U]** whether that is a scoping rule or an artefact of generation.

### Heuristics a solver will need
- **H1 [U]** Recover the account (and identity-free analytics) from the deletion-worker batch in `system_activity.csv`. That means the `DELETE_RECORD … submitted primary profile account_N` row 0–3 days after `requested_at` that precedes, by about 0.5–3 h, the actions on resources already tied to the subject. Batches of different cases interleave, so this is ambiguous when several cases are processed at once (27 vs 28).
- **H2 [U]** When an audit record can't be found in visible evidence, omit the `VERIFY_RECORD_PRESENT` step (partial-credit loss) or look in late files for linking only (see R7).
