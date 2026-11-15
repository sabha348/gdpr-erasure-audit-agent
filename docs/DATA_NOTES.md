# DATA_NOTES — Ghost Data Still Exists

Notes from a full read of `AGENTS.md`, `README.md`, `problem_statement.md` and every
participant-facing file in `dataset/` (CSV profiling, all 94 evidence files listed,
representative files of every format opened). No solution code yet.

Counts quoted below were measured, not estimated.

---

## 1. Inventory

| File | Rows / files | Notes |
|---|---|---|
| `deletion_requests.csv` | 518 | `case_33` … `case_550`; file is in **numeric** order, not lexicographic |
| `sample_deletion_requests.csv` | 32 | `case_01` … `case_32` (no ID overlap with eval) |
| `sample_output.csv` | 32 | 10 `COMPLETE`, 22 `INCOMPLETE`, 0 `NOT_APPLICABLE` |
| `user_identities.csv` | 850 | `user_01` … `user_850` (1–9 zero-padded: `user_01`; ≥10 not padded) |
| `user_audit_log.csv` | 306 | 107 email, 107 email_hash, 92 phone changes |
| `system_activity.csv` | 4011 | global stream, 3948 distinct `resource_id`s, ≤2 events per resource |
| `deletion_rules.csv` | 15 | one rule per data category |
| `evidence_files.csv` | 94 | `evidence_01` … `evidence_94`; every path exists |
| `output.csv` | header only | template |
| `evidence/databases/*.sql` | 24 | SQLite-compatible text dumps |
| `evidence/logs/*.log` | 26 | 6+ distinct line formats |
| `evidence/records/*.json|*.txt` | 28 JSON + 1 TXT | |
| `evidence/documents/*.pdf` | 4 | all have an extractable text layer |
| `evidence/images/*.png` | 11 | rendered admin-console screenshots, no text layer |

Tooling available on this machine: Python 3.12 (stdlib `sqlite3`, `json`, `csv`),
`pdftotext` (poppler, mingw). **Not** available: `sqlite3` CLI, `tesseract`, Pillow,
pypdf/pdfplumber, any LLM SDK. Note that the solution can't assume OCR is installed.

---

## 2. CSV schemas

### 2.1 `deletion_requests.csv` / `sample_deletion_requests.csv`
```
case_id,email,requested_at,reported_status,reported_at,audit_at,request_text
```
- `reported_status`: COMPLETE 254 / PARTIALLY_COMPLETE 152 / NOT_STARTED 112 (eval). It is only a claim.
- Always `requested_at ≤ reported_at ≤ audit_at`.
- `audit_at` range 2026-01-29 → 2026-10-01; spread roughly evenly across months.
- `email` matches a **current** identity in 418 eval cases and a **historical** (audit-log `old_value`) email in 100. It always matches one of the two, and never both.
- Some request emails are **upper-cased** (e.g. `SIPHO.MOKOENA@GMAIL.COM`), and evidence uses the same upper-case form in places. Email matching must be case-insensitive.
- One email appears twice: `ingrid.dahl@outlook.com` → `case_33` (audit 07-19) and `case_52` (audit 06-19). Both belong to the same subject and have different audit cut-offs, so they may need different answers.
- `request_text` languages: English (most), Portuguese, Spanish, French, Japanese, Korean (12–14 each). There are 27 distinct opening sentences. **10 eval requests are non-deletion** (phone correction, data export/portability, invoice copy, email change, login help, surname fix, marketing opt-out "only", "stop promotional emails", access request ×2). That count matches the 10 `CLASSIFY_REQUEST … non-deletion` rows in `system_activity.csv`. "Please remove me from marketing lists only" contains the word *remove* but is **not** a deletion request, so keyword matching alone is not enough.

### 2.2 `user_identities.csv`
```
user_id,name,email,phone,email_hash,updated_at
```
- `email_hash = sha256(lower(email))`, verified on 850/850 rows.
- Phones are free-form international strings (`+1 (202) 555-0103`, `+82 10 2466 4678`, …). Normalise to digits if matching phones.
- There are many near-namesakes (8 users named "Ingrid …"), so a name alone is not a safe join key.

### 2.3 `user_audit_log.csv`
```
audit_id,user_id,event_time,field_name,old_value,new_value
```
- `field_name ∈ {email, email_hash, phone}`. An email change is always paired with an email_hash change that has the same timestamp.
- Per user: 102 users have one email change, 2 have two (a chain A→B→C), 91 have one phone change, and 1 has both.
- No historical email maps to more than one user, and none is anyone's current email.
- No historical email used by a request was changed *after* that case's `audit_at`.
- Historical **hashes** matter. Example from sample case_12: `analytics_9914` is linked only via `correlation_key="44a65154…"`, which is `user_12`'s *old* email_hash (audit_283).

### 2.4 `system_activity.csv`
```
activity_id,event_time,event_type,source_system,target_system,resource_id,reported_result,details
```
- `event_type` counts: DELETE_RECORD 890, UPDATE_RECORD 672, SYNC_RECORD 447, INDEX_RECORD 400, SEND_EMAIL 368, REFRESH_CACHE 284, RETAIN_RECORD 206, ANONYMIZE_RECORD 204, REDACT_RECORD 173, CREATE_EXPORT 155, CHECK_DATA_FLOW 89, DISABLE_DATA_FLOW 36, BLOCK_RESTORE 36, CHECK_RESTORE_ACCESS 28, LINK_RESOURCE_IDENTITY 12, CLASSIFY_REQUEST 10, RESTORE_BACKUP 1.
- `reported_result`: SUCCESS 2914 / FAILED 721 / PARTIAL 376. These are claims.
- Rows carry **no user identifier**, only `resource_id` (plus the 12 `LINK_RESOURCE_IDENTITY` rows). A row joins to a user only after evidence has tied that `resource_id` to the user.
- About 47 detail templates, e.g. "Deletion worker submitted primary profile account_N…", "Connector status check confirmed that data flow data_flow_N remains enabled.", "Restore-access check confirmed that archive backup_N can still be restored.", "Active connector repopulated downstream resource search_N."
- Rows are time-ordered **except** `activity_4000`–`activity_4011` (LINK rows), which are appended at the end with earlier timestamps.
- `activity_3387` RESTORE_BACKUP has `resource_id=backup_9287`, but its details say it "repopulated downstream resource search_4882". The column and the details text name different resources.
- `CHECK_DATA_FLOW` (89) and `CHECK_RESTORE_ACCESS` (28) are the activity-based evidence for `REPORT_ACTIVE_DATA_FLOW` / `REPORT_RESTORE_RISK`. `DISABLE_DATA_FLOW` / `BLOCK_RESTORE` rows are claims that the flow or restore has been turned off.
- System naming is inconsistent: `object_store` (export_service target) vs `object_storage` (LINK row), and `profile_service` is a source of `UPDATE_RECORD` on `account_service`.

### 2.5 `deletion_rules.csv`
```
rule_id,data_category,condition,required_action
```
| category | action | condition |
|---|---|---|
| USER_PROFILE, SECONDARY_IDENTIFIER, SEARCH_INDEX_ENTRY, CACHE_ENTRY, USER_EXPORT, MARKETING_PROFILE | DELETE | Always |
| SUPPORT_MESSAGE | REDACT | conversation has shared customer/agent/order context to retain |
| SUPPORT_ATTACHMENT | DELETE | attachment belongs only to the subject |
| ANALYTICS_PROFILE, PRODUCT_TELEMETRY | ANONYMIZE | still attributable |
| EMAIL_DELIVERY_LOG | REDACT | log contains user email |
| SECURITY_AUDIT_LOG | RETAIN | within supplied retention period |
| LEGAL_HOLD_RECORD | RETAIN | legal hold active |
| BACKUP_ARCHIVE | RETAIN | restore access can be blocked |
| ACCOUNT_TOMBSTONE | REDACT | only stable user_id needed |

Category is **not** stored on evidence rows (except the `record_type` column in handover/manifest-style records). It must be inferred from system and collection:
`account_service/customer_profiles`→USER_PROFILE, `search_service`→SEARCH_INDEX_ENTRY,
`analytics_platform`→ANALYTICS_PROFILE, `support_platform/support_conversations`→SUPPORT_MESSAGE,
`support_platform/ticket_attachments`→SUPPORT_ATTACHMENT, `export_service`→USER_EXPORT,
`security_service`→SECURITY_AUDIT_LOG, `legal_service`→LEGAL_HOLD_RECORD,
`backup_service`→BACKUP_ARCHIVE, `identity_service/deleted_customer_references`→ACCOUNT_TOMBSTONE,
`marketing_platform`→MARKETING_PROFILE, `integration_service`→data flow (not a data category).

### 2.6 `evidence_files.csv`
```
file_id,file_path,origin,captured_at
```
- `file_path` is relative to `dataset/` (`evidence/...`).
- `captured_at` is a file-level timestamp. The sample answers show the visibility rule is **inclusive**: `captured_at ≤ audit_at`. Sample case_14 cites `evidence_03`, captured at exactly its `audit_at` of 2026-10-01T12:00:00Z. 268 of 518 eval cases have an `audit_at` exactly equal to some file's `captured_at`.
- Some evidence IDs are hand-crafted fixtures used by the low-numbered sample cases and the `user_03…user_22` stories: `evidence_54…61` and the images/PDFs.

### 2.7 `sample_output.csv` (format oracle)
- `steps_json` is serialized with compact JSON; key order varies between rows, which the grader ignores.
- **Ordering is confirmed**: phase (FINDING → REMEDIATION → VERIFICATION), then **operation order as listed in the operations table**, then `system_id` asc, then `resource_id` asc. Example from case_11 verification: ABSENT(account, search) → PRESENT(security) → REDACTED(support) → ANONYMIZED(analytics) → DISABLED(integration).
- Remediation order follows the table too: DISABLE_DATA_FLOW / BLOCK_RESTORE come before DELETE / REDACT / ANONYMIZE, which satisfies "disable flows first".
- **Every** deletion case's VERIFICATION includes `account_service/<account> VERIFY_RECORD_ABSENT`. This holds even when the account was already gone and so produced no finding.
- `security_service/audit_record_* VERIFY_RECORD_PRESENT` appears whenever an audit record for the subject is visible (24/32). It is never a finding.
- INCOMPLETE rows: FINDING steps cite one evidence id, while remediation and all verification steps use `""`. COMPLETE rows: verification only, all citing evidence.
- In 31 of 32 samples, all FINDING steps of a case cite **the same single file**: the latest visible snapshot that shows the whole picture. Most common are `evidence_92`, `evidence_94`, `evidence_03`, `evidence_01`, and `evidence_05`.
- REPORT_REMAINING_RECORD vs REPORT_EXPOSED_PII follows the rule action. DELETE categories → REMAINING_RECORD; REDACT/ANONYMIZE categories → EXPOSED_PII. The exception is case_12, where an analytics record is DELETEd (see §6).

---

## 3. How identities link across files

```
request.email ──(case-insensitive)──► user_identities.email            (current)
              └──────────────────────► user_audit_log.old_value (email) (historical) ─► user_id
user_id ──► user_identities: current name / email / phone / email_hash
        └─► user_audit_log: every old email, old email_hash, old phone (follow chains)
```
The subject's full identifier set is {user_id, current + all historical emails, current + historical
email_hashes, current + historical phones, name}. Evidence refers to subjects by **any** of these:

| Evidence field | Holds |
|---|---|
| `customer_reference`, `subject_reference`, `user_id`, `customer key`, `correlation_key="user_N"`, `lookup_key=user_N` | user_id |
| `email`, `owner_email`, `requester_email`, `customer_email`, `uploaded_by`, `subject_selector`, `lookup_key`, `match_key`, `subject_key`, `owner_key`, `scan_key`, `matched_value`, `correlation_key`, `lookup_value`, `subject_reference` (in handover / manifest records) | email (any case) |
| `email_hash`, `correlation_key=<64-hex>` | sha256 of current **or historical** email |
| `message_text` / `message_excerpt` free text | email embedded in prose ("Please send the delivery update to X.") |
| `backup_catalog.subject_reference` | sometimes an email, sometimes a user_id |

Resource IDs (`account_N`, `search_N`, `analytics_N`, `support_N`, `export_N`, `data_flow_N`,
`backup_N`, `audit_record_N`, `tombstone_N`, `legal_hold_N`, `marketing_N`, `object_N`) are the
join key from evidence to `system_activity.csv`. Note:
- Account resource IDs are **not** `account_<user number>`. For example, user_12 owns account_9797 and user_53 owns account_994.
- In log traces the email often appears only on the `*_started` line (`lookup_key=`), while the `resource_id` appears on the `*_completed` line. **Join the two by `trace_id` / `requestId` / `[tr-…]`.**
- Search documents can link back to the source account via `source_resource_id` (customer_search_primary).
- Decoys exist. Use **exact** matching after lower-casing, never fuzzy matching:
  - near-miss emails such as `g.silva76.jr@gmail.com` next to `g.silva76@gmail.com`, `smartinez6461.jr@…`, `camila.almeida.jr@…` (≥10 `.jr` variants);
  - lookalike names: `Jonas Berglund` / `Joana Berg` next to `Jonas Berg`; `Mateo Rios Gil`; `Priya Ramanathan`;
  - user_ids that do not exist in identities (`user_7204`, `user_8741`, `user_9116`);
  - records like `customer4104@yahoo.com` / `nadia.popescu@yahoo.com` sitting next to the real `nadia.ionescu@yahoo.com`.
- The `LINK_RESOURCE_IDENTITY` activity rows explicitly link a few image/PDF resources to user + email: account_5762→user_11, search_428→user_13, data_flow_1121→user_14, analytics_7438→user_20, export_1260 and export_1842→user_18, support_1034→user_22, backup_9287→user_16, object_19→user_07, marketing_001→user_09, audit_record_003→user_03, tombstone_17→user_17.

---

## 4. Evidence types: contents and parsing method

### 4.1 SQL snapshots (`databases/`, 24 files) — plain parsing (load into in-memory `sqlite3`)
Every file runs cleanly with `sqlite3.executescript`. Three schema families:

1. **Generic JSON-document topology dumps.** These are `customer_data_reporting_replica_*`, `customer_data_regional_replicas_*`, `customer_data_read_replica_*`, and `customer_data_failover_replica_20260920`.
   Tables `customer_data_primary` + one or more replicas (`customer_data_read_replica`,
   `customer_data_reporting_replica`, `customer_data_replica_ap_south`, `customer_data_replica_eu_west`,
   `customer_data_failover_replica`), each with
   `(resource_id, origin_system, collection_name, record_json, updated_at)`.
   Collections: customer_profiles, customer_search_documents, customer_analytics_reports,
   support_conversations, ticket_attachments, customer_export_jobs, connector_configurations
   (`enabled`, `subject_selector`), backup_catalog (`restore_allowed`), security_audit_events,
   retained_order_records (legal holds), deleted_customer_references (tombstones).
   **Replication drift is the main signal.** Typically the replica holds an extra
   `account_service/customer_profiles` row that is absent from primary. Sample case_16: `account_994` exists only in the read replica, and it is a finding. Header comments give the capture time and topology.
2. **Normalised inventory snapshots** (`customer_data_inventory_*`, 11 files). Typed tables: `customer_profiles`, `customer_search_documents`, `customer_analytics_profiles`, `support_records`, `customer_exports`, `connector_subscriptions (enabled 0/1)`, `backup_restore_entries (restore_allowed 0/1)`, `security_audit_records (retention_until)`, and **`snapshot_scope`**, which lists which systems the snapshot covers. Scope varies: some files omit `export_service` or `backup_service`, and `20260905` covers only `account_service` and is otherwise empty. A trailer comment `-- snapshot_completed_at=…` closes each file. `account_status` can be `DELETION_PENDING`.
3. **Small bespoke dumps.** `customer_accounts_failover_replica_20260902` has `customer_profiles_primary` and `_failover_replica`; the failover replica still holds `account_005` (sample case_05). `customer_search_primary_20260910` holds `search_documents_primary` with `source_resource_id` (sample case_06, plus two lookalike decoys).

### 4.2 Logs (`logs/`, 26 files) — plain parsing (regex per format)
Line formats seen:
- `TS [LEVEL] [service] [trace] event: k="v" …` (application_trace_*)
- `TS level=L service=S … event=E k=v` logfmt (service_events_*, resource_lookup_trace_*, support_replica)
- `TS {json}` (structured_service_events_*)
- `TS <pri>1 node-N service trace - - level=… k="v"` RFC5424 (syslog_archive_*)
- `TS | LEVEL | service | trace | event | k="v"` (operations_stream_*)
- `TS requestId=… level=… function=svc message="k=\"v\" …"`, with escaped quotes (cloud_runtime_export_*)
- `TS INFO service=… event=record_lookup seq=N system="…" resource_id="…" record_type="…" result="FOUND|NOT_FOUND" subject_reference="…"` (search_index_diagnostic, notification_delivery)

State-bearing events:
- `profile_lookup_completed` with `rows`/`http_status` (404 means absent, 200 means present);
- `index_query_completed result=hit`;
- `analytics_profile_read(_completed)` with `direct_identifiers`/`email`/`phone`/`email_hash`;
- `analytics_identity_scan matched_value=`;
- `contact_lookup_completed`;
- `message_pii_scan` / `attachment_pii_scan` `matched_value=` (`file=`);
- `support_ticket_read_completed` with `requester_*`;
- `export_download_check_completed available=` and `export_metadata_read_completed`;
- `connector_heartbeat` / `connector_config_loaded` / `connector_config_read_completed` with `enabled=`, `scheduler_registered=`;
- `restore_candidate_evaluated eligible= deletion_block=`;
- `restore_authorization_completed decision=`;
- `restore_policy_read_completed restore_allowed=`;
- `audit_record_read_completed`;
- `retained_order_query_*`;
- `deletion_marker_check marker_found=false`.

Noise lines come from `log_router`, `dns_cache`, `secret_agent`, `webhook_delivery`, `thread_pool`, and others. `cloud_runtime_export_af_south_20260930` contains 300 `profile_service` lookups with 5-digit `account_NNNNN` IDs, which look like bulk noise.
**Line timestamps are not the file `captured_at`.** Files span 1–7 days, and the same resource can flip state inside one file. For example, in `search_index_diagnostic` account_8473 is FOUND on 08-31 and NOT_FOUND on 09-06. The latest line per resource wins.

### 4.3 JSON / TXT records (`records/`) — plain parsing
- `customer_data_snapshot_bundle_*.json` (12 files): `{exported_at, snapshots:[{captured_at, data_store, collection, records:[…]}]}`. **Each inner snapshot has its own `captured_at`**, which can be days earlier than the export. For example, the 0917 bundle carries an account_service snapshot from 09-09 showing account_5684 ACTIVE and another from 09-15 without it. Some bundles also include `operational_record_history` collections (record_type/result/lookup_value rows).
- `service_resource_export_*.json` (11 files): `{exported_at, service_exports:{account_service:{profile_lookups:[…status_code…]}, analytics_platform:{profiles}, backup_service:{restore_entries}, export_service:{exports}, integration_service:{connectors}, search_service:{index_queries:{query, hits}}, security_service:{audit_records}, support_platform:{tickets}}}`, with per-item `observed_at`. These are the main source for sample answers (`evidence_92` alone is cited 36 times).
- Record-ledger style (`connector_configurations_20260812`, `customer_export_manifest_20260409`, `security_audit_ledger_20261001`): `{records:[{recorded_at, system, resource_id, record_type, result, subject_reference}]}`. `result` values are FOUND, NOT_FOUND, IDENTIFIABLE, ANONYMIZED, REDACTED, ACTIVE, DISABLED, and BLOCKED. Despite the file names, the content is generic.
- `customer_account_handover_20261001.txt`: the same ledger as pipe-separated text lines (`TS | system | resource | RECORD_TYPE | RESULT | subject`).
- `customer_analytics_report_20260907.json`, `customer_export_jobs_20260914.json`: single-collection captures for samples 04 and 08.

### 4.4 PDFs (`documents/`, 4 files) — text extraction is enough (`pdftotext`), but layout is scrambled
All four PDFs have a real text layer. `pdftotext -layout` interleaves label and value columns, and time strings get split ("issued 05 Sep 2026 06 … 30 UTC"). Extract IDs and emails with regex rather than trusting the layout.
- `account_history_report_20260718.pdf`: customer statement for Ingrid Dahl, user_18, statement `NST-2026-0718-1842`, which the LINK row maps to `export_1842`.
- `delivery_correction_attachment.pdf`: support attachment `support_001` / CS-4827 with Priya Raman's email and phone (sample case_07: DELETE the attachment).
- `legal_retention_register_20260529.pdf`: disputed-order table rows `ORD | user_NN | reason | date | amount | Retain until …` for user_08/12/17/21/24/29 (LEGAL_HOLD_RECORD → RETAIN).
- `security_retention_extract.pdf`: `audit_record_003` for user_03, "retain until 25 Aug 2027" (SECURITY_AUDIT_LOG → RETAIN).

### 4.5 Images (`images/`, 11 PNGs, 1500–1800 px) — **need vision/OCR**
These are rendered admin-console screenshots with no embedded text, and Pillow and tesseract are not installed. Every screenshot has a header (name, ID line), KPI cards, a table and a right-hand detail panel. Facts read from each:

| file | system → resource | subject | key state |
|---|---|---|---|
| account_admin_console_20260926 | account_service account_5762 | Haruka Sato, user_11, haruka.s1838@zoho.com | live profile with orders, support_9538 |
| analytics_profile_view_20260713 | analytics_platform analytics_7438 | Omar Darwish, user_20, darwish.omar@icloud.com | identifiable analytics profile |
| backup_restore_console_20260429 | backup_service backup_9287 | areed1433@gmail.com (user_16) | archive listed, restores completing (no block) |
| connector_management_console_20260807 | integration_service data_flow_1121 | jain.aditi@gmail.com (user_14); also data_flow_9846 lucia-torres | continuous sync "Healthy" (active) |
| customer_export_console_20260618 | export_service export_1260 | ingrid.dahl@outlook.com (user_18) | export "Ready" |
| deleted_account_reference_20260310 | identity_service, user_17 | "Retained reference", no PII shown | tombstone-like reference |
| marketing_profile_console (no date in name; manifest 09-16) | marketing_platform marketing_001 | Soo-jin Park, sj.park@naver.com, +82… | live marketing profile |
| object_storage_browser_20260213 | object storage delivery_confirmation_19.pdf | (user_07 via LINK row) | attachment present |
| search_index_inspector_20260901 | search_service search_428 | Gabriel Silva, user_13, g.silva76@gmail.com; decoy search_1874 `g.silva76.jr@…` | indexed |
| security_audit_explorer_20260905 | security_service audit_record_001…006 | user_03 | signed events (RETAIN) |
| support_ticket_console_20260524 | support_platform support_1034 | Kofi Agyeman, kagyeman8947@fastmail.com (user_22) | live conversation with agent context |

Because there are only 11 images and they are deterministic, practical options are: (a) a one-off vision-LLM extraction cached to a JSON/Markdown sidecar inside `code/`, or (b) an optional OCR path. The screenshots are tied to users through the `LINK_RESOURCE_IDENTITY` activity rows, so the activity CSV already gives the resource↔user link. The image supplies the **state**.

**Summary.** OCR/vision is needed for the 11 PNGs only. PDFs need `pdftotext` or a PDF library, with no OCR. SQL, logs, JSON and TXT are plain parsing.

---

## 5. Worked join examples (from sample answers)

- **case_16** (diya.k4436@outlook.com → user_53). `customer_data_read_replica_20260921.sql` (evidence_05, 09-21 ≤ audit 09-21T10:00) shows `account_994` in the read replica only and `search_1007` in both tables, so both are REMAINING_RECORD. `audit_record_9528` (subject user_53) is the retained audit record.
- **case_12** (minjun.kim@gmail.com, current; old email minjun.kim90@…). In `service_events_ap_south_20260915.log` (evidence_01), the lookup `minjun.kim@gmail.com` and `account_9797` are joined by trace_id and return 404, so the account is absent. `analytics_9914` matches through the **old email_hash**, `audit_record_2945` through `correlation_key="user_12"`. Activity shows `DELETE_RECORD analytics_9914 FAILED`.
- **case_32**. Bundle `20260915` (evidence_51) has `tombstone_9178` (user_542) still holding name, email, phone and hash, which gives EXPOSED_PII → REDACT. `tombstone_440` is all-null, so for case_31 it is VERIFY_RECORD_REDACTED.
- **case_05**. `customer_accounts_failover_replica_20260902` (evidence_54): `account_005` is in the failover replica but not in primary. Activity_3483 shows the replication checkpoint.

---

## 6. Ambiguities and open questions

1. **Row sort order.** The spec says "sort by case_id", but the input file is numeric-ordered (`case_33…case_99, case_100…`), and lexicographic order would put `case_100` before `case_33`. Samples are zero-padded, so they don't settle it. **Default: keep numeric order (as the input), and confirm before submission.**
2. **Visibility boundary.** Inclusive `captured_at ≤ audit_at`, as case_14 shows. It is unclear whether a file captured before `audit_at` but containing *inner* timestamps after it (bundles, logs) is fully usable, and whether a file captured after `audit_at` with inner rows before it may be used. The rules forbid citing a file captured after `audit_at`, so treat the file-level `captured_at` as the gate and inner timestamps as ordering within the file.
3. **Which file to cite.** One resource is often visible in several files, but the grader expects one `evidence_id`. The samples suggest citing the latest visible file that shows the resource's state. In 31/32 samples, all steps of a case cite one file. How to break ties between files captured at the same time is unknown.
4. **Latest-state-wins vs any-copy-present.** Earlier snapshots show records that later snapshots don't (case_26: an "old report" says the account existed, yet the answer is COMPLETE). Drift between primary and replica in the *same* snapshot counts as remaining (case_16). Is a record seen in an older file but absent from the newest file absent, or just unobserved? This interacts with `snapshot_scope`: an inventory snapshot that does not cover a system is not proof of absence.
5. **DELETE vs ANONYMIZE for analytics.** Rule 07 says ANONYMIZE, but sample case_12 DELETEs `analytics_9914`. That record was found via a `contact_lookup_completed … email_opt_in=true` event, possibly signalling a contact or marketing record, or a SECONDARY_IDENTIFIER keyed on the old hash. The rule that decides between the two is not documented.
6. **SUPPORT_MESSAGE: REDACT vs DELETE.** Rule 05 REDACTs only when shared context must be retained, and rule 06 DELETEs attachments that belong only to the subject. Samples have both outcomes (support → DELETE in case_10 and case_13, REDACT in case_11 and case_19). The deciding signal appears to be collection or record type (ticket_attachments / SUPPORT_ATTACHMENT, `attachment_pii_scan`, `support_replica` legacy contact index) vs conversation (`message_pii_scan`, "message_text" with order context), but this is not stated.
7. **Mandatory verification set.** `account_service VERIFY_RECORD_ABSENT` appears in every deletion case, even when no account resource is visible as a finding. Its `resource_id` must be found from a 404 lookup or an earlier snapshot. It is unclear what to emit if no account resource ID can be found at all. `security_service VERIFY_RECORD_PRESENT` appears only when an audit record is linked. Legal holds (`legal_hold_*`) never appear in sample answers, so how to verify them (VERIFY_RECORD_PRESENT?) is unknown.
8. **Retention expiry.** `retention_until` / "Retain until" dates are all far in the future (2027–2034). Rule 12 only retains *within* the period, and no case seems to exercise expiry.
9. **Data flows keyed on other identifiers.** `subject_selector` / `customer_email` can be an upper-case email, a historical email, or a user_id. A connector shown `enabled=false` but with `CHECK_DATA_FLOW … remains enabled` in activity creates a conflict. Precedence between activity claims and evidence state is unspecified (rule: evidence > claims).
10. **Restore risk sources.** `restore_allowed=1/true` (SQL, JSON), `CHECK_RESTORE_ACCESS` activity, `restore_candidate_evaluated eligible=true deletion_block=false` logs, `BACKUP_ARCHIVE | ACTIVE` ledger rows, and the backup console screenshot. It is unclear which counts as "evidence that proves" the risk, and which file to cite when several agree.
11. **Tombstone partial redaction.** Some tombstones have only some fields nulled (e.g. email null but name + phone + hash present). Any non-null direct identifier should be EXPOSED_PII.
12. **Inconsistent system IDs.** `object_store` vs `object_storage`; `support_replica` service in logs vs `support_platform` system_id in answers (sample case_10 uses `support_platform/support_002` citing `support_replica.log`); `profile_service` emitting `account_*`. Output must use the canonical `system_id` names that appear in sample outputs: account_service, search_service, analytics_platform, support_platform, export_service, integration_service, backup_service, security_service, identity_service (plus probably legal_service, marketing_platform, object_storage).
13. **Activity column vs details mismatch.** `activity_3387` names backup_9287 as resource_id while its details name search_4882. It is unclear which to treat as the affected resource.
14. **Same subject, two cases.** `ingrid.dahl@outlook.com` (case_33, case_52) is evaluated at two different `audit_at` values. The export_1260 screenshot (06-18), the statement PDF (07-18) and the export_1842 link (07-18) fall on different sides of the two cut-offs.
15. **Non-deletion detection.** 10 requests are non-deletion, but some still contain "remove" ("remove me from marketing lists only"). The CLASSIFY_REQUEST activity rows reference account IDs, not emails, so they can corroborate a classification only after account resolution. The 6 non-English deletion templates must still be recognised as deletion.
16. **Empty or partial snapshots.** `customer_data_inventory_20260905` is empty apart from its scope row, and `resource_lookup_trace_20260310` has a single query. `log_router export_completed … dropped_auxiliary=N` hints that some lines were dropped, so absence from a log is weak evidence.
17. **Output key order.** Sample rows use two different key orders. The grader ignores this, but serialize deterministically: fixed key order, compact separators.
18. **Not in samples.** Several cases have no sample to calibrate against: NOT_APPLICABLE; marketing_platform, legal_service and object storage resources; email delivery logs (EMAIL_DELIVERY_LOG has a rule but `email_delivery_*` resources appear only in activity); and cache entries (`cache_*` appear only in activity).

---

## 7. Implications for the build (to decide later)

- Deterministic core: identity resolver, evidence indexer (SQL→sqlite, log regexes with trace joins, JSON walkers, pdftotext), per-case time-filtered state reconstruction, rule engine, step serializer.
- An LLM is useful mainly for (a) request-intent classification across 6 languages and (b) one-time image fact extraction. Both are small and cacheable, which keeps token cost low.
- Build a local scorer against `sample_output.csv` early, at case, step, field and sequence level, for `evaluation/evaluation_report.md`.
