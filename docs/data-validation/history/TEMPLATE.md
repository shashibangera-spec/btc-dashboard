# Validation History Record

> Copy this file to `docs/data-validation/history/YYYY-MM-DD-validation.md` for each distinct validation event. Keep completed history records immutable; append a new dated record for a new run or correction.

## 1. Validation identity

- **Validation ID:**
- **Repository:**
- **Repository role:**
- **Validation date/time (UTC):**
- **Reviewer/operator:**
- **Branch:**
- **Outcome:** PASS / CONDITIONAL / OPEN / FAIL / NOT VERIFIED
- **Previous related validation:**
- **Scope:**
- **Explicit exclusions:**

## 2. Exact source version

- **Full Git commit SHA:**
- **Relevant source/document paths:**
- **Relevant blob SHA(s):**
- **Pull request:**
- **GitHub Actions run URL:**
- **Workflow / job:**
- **Deployment identity and revision (if applicable):**

Do not use a branch name alone as source identity.

## 3. Data identity and provenance

- **Provider/source:**
- **Symbol/instrument:**
- **Dataset or archive identity:**
- **Date/time range:**
- **Timeframe:**
- **Session definition and timezone:**
- **Archive/file size:**
- **SHA-256/fingerprint:**
- **Database project / branch / database:**
- **Schema / table:**
- **Migration or schema snapshot identity:**

Use NOT CHECKED for fields that were not verified.

## 4. Dataset quality

| Check | Expected | Observed | Result / evidence |
|---|---:|---:|---|
| Rows/records | | | |
| Unique records | | | |
| Missing intervals/slots | | | |
| Duplicates | | | |
| Conflicts | | | |
| Invalid/out-of-range records | | | |
| First timestamp | | | |
| Last timestamp | | | |

Do not fill in inferred values. Use NOT CHECKED where no check was performed.

## 5. Checks actually performed

For each check, record the exact check ID, command/query/test name, result and evidence.

| Check ID | Exact command / query / test case | Result | Evidence |
|---|---|---|---|
| | | | |

For automated tests, include:
- Exact test file paths and exact test case names.
- Command used and working directory.
- Collected, executed, passed, failed, skipped and deselected counts.
- GitHub Actions run URL, workflow, job and commit SHA.

A test specification or workflow file is not proof that a test executed.

## 6. Findings

| Finding ID | Severity | Description | Evidence | Status |
|---|---|---|---|---|
| | | | | |

## 7. Accepted conclusions

List only conclusions supported by the exact source commit, data version and evidence recorded above. State the scope and qualification alongside each conclusion.

## 8. Qualifications and limitations

Record untested conditions, sample-size limits, stale or missing fields, timezone/deployment caveats, synthetic fixtures, external dependencies and any reason the result must remain conditional.

## 9. Open work

| Item ID | Required next step | Owner | Evidence needed |
|---|---|---|---|
| | | | |

## 10. Safety and execution record

- **Read-only repository inspection:** YES / NO
- **Offline tests only:** YES / NO
- **Isolated test database used:** YES / NO / NOT APPLICABLE
- **Production database queried:** YES / NO
- **Any database writes:** YES / NO
- **Collector/exchange endpoint executed:** YES / NO
- **Production workflow dispatched:** YES / NO
- **Deployment performed:** YES / NO
- **Orders/account actions performed:** YES / NO
- **Other side effects:**

Explain any YES answer and link the exact authorization/evidence. Never claim read-only if writes or external side effects occurred.

## 11. Final decision

- **Decision:**
- **What this validation establishes:**
- **What it does not establish:**
- **Conditions for a later PASS or closure:**

## 12. Evidence links

- Source commit:
- Pull request:
- Actions run:
- Test report:
- Data/archive manifest:
- Database evidence:
- Deployment evidence:
- Related prior validation:
