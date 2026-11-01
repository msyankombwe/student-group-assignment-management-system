# Requirements traceability — UNILUS SGAMS

Status key: **Done** · **Partial** · **Outstanding** · **Tested**

| ID | Requirement | Status | Notes |
| --- | --- | --- | --- |
| FR-01 | User Authentication | Done / Tested | Django auth login/logout; password hashing |
| FR-02 | User Profile Management | Partial | Profile view; edit forms limited |
| FR-03 | Student Management | Partial | Admin + seed; dedicated admin UI expanding |
| FR-04 | Lecturer Management | Partial | Role on User; admin site |
| FR-05 | Course Management | Done / Tested | Create course; lecturer ownership |
| FR-06 | Assignment Creation | Done | Create/publish fields; lecturer scoped |
| FR-07 | Group Formation | Done / Tested | Lecturer creates groups |
| FR-08 | Group Membership | Done / Tested | Add members; private access enforced |
| FR-09 | Task Allocation | Done / Tested | Tasks link to the assignment through their group; assignees must be approved group members; creator, priority, status, and update timestamps are recorded and exposed by the API |
| FR-10 | Internal Deadlines | Done / Tested | Task deadlines are required; Overdue is derived consistently and is not stored as a task status |
| FR-11 | File Management | Partial / Tested | Evidence upload/download; group files model ready |
| FR-12 | Group Communication | Done | Stored chronological messages |
| FR-13 | Progress Monitoring | Partial | Dashboard + group progress % from tasks |
| FR-14 | Contribution Tracking | Partial | ActivityLog on key actions; fairness disclaimer shown |
| FR-15 | Notifications | Done / Tested | In-app notifications + mark read |
| FR-16 | Assignment Submission | Done / Tested | Group submission, receipt, optional Moodle reference, protected history and downloads |
| FR-17 | Submission Review | Done / Tested | Lecturer submission list by managed course/assignment; late and prior versions shown |
| FR-18 | Grading and Feedback | Done / Tested | Restricted lecturer assessment; student feedback visible only after release |
| FR-19 | Peer Evaluation | Done / Tested | Configurable criterion scores; confidential lecturer summary; no self-eval or duplicates |
| FR-20 | Reporting | Outstanding | Models support data; dedicated report views next |
| FR-21 | Administration | Partial | Django admin + audit log writes |
| FR-22 | Analytics | Partial | Live dashboard counts; Chart.js loaded |

| ID | Non-functional | Status | Notes |
| --- | --- | --- | --- |
| NFR-01 | Usability | Partial | Bootstrap responsive UI |
| NFR-02 | Security | Partial | Server-side checks; env secrets; harden API further |
| NFR-03 | Confidentiality | Partial / Tested | Cross-group access denied in tests |
| NFR-04 | Integrity | Partial | Validation on forms/scores/uploads |
| NFR-05 | Availability | Outstanding | Depends on hosting |
| NFR-06 | Performance | Outstanding | Not load-tested |
| NFR-07 | Scalability | Outstanding | SQLite for demo; Postgres path documented |
| NFR-08 | Maintainability | Partial | Modular `core` app + docs |
| NFR-09 | Reliability | Partial | Automated workflow tests passing |
| NFR-10 | Compatibility | Partial | Modern browsers via Bootstrap |

Last verified automated tests: `python manage.py test core` — **10 passed**.
