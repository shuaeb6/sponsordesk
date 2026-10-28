# Build Log

Decisions, failures, and fixes, in the order they happened.

## 2026-10-27 · Phase 1 + deployable slice

### Decisions

| Decision | Options considered | Chosen | Why |
|---|---|---|---|
| Hosting | Cloudflare, Azure Container Apps, AWS ECS Fargate | ECS Fargate (ARM64) behind an ALB | Matches the target team's AWS stack and the CloudWatch plan. ~$45/mo; `deploy/ecs/teardown.sh` removes billable resources between demos. |
| Agent model | Opus 5.5, Sonnet 5.5, Haiku 5.5 | Opus 5.5, configurable via `AGENT_MODEL` | Best routing and drafting quality; per-ticket cost is small at demo volume. |
| Repo layout | Single package vs. uv workspace | uv workspace: `core`, `synth`, `redact` (piiguard), `console` | The redactor must be publishable on its own; the workspace keeps its dependencies separate. |
| Synthetic identifiers | Faker defaults vs. reserved ranges | SSNs in 9xx (never issued), EINs with `00` prefix, phones 555-01xx, `.example` emails | Guarantees no generated value can belong to a real person; tests enforce it. |
| Ground truth | Labels inside tickets vs. separate files | `ground_truth/` directory, never served by the API | Evals can't be gamed by a model reading its own labels. |
| Payroll defects | Random noise vs. planted defects | Mutable drafts; ticket scenarios plant the exact defect they describe, then status and validation messages are derived from the rows | Ticket text, file contents, and labels cannot drift apart. |
| Close guard | Prompt instruction vs. code | Code: `check_close()` rejects unless status is `resolved`, every criterion is confirmed, and a note exists | A prompt can be ignored; a 409 cannot. Every blocked attempt is audited. |
| Deadline scope | Full 9 phases vs. a deployable slice | Slice: generator, redactor, baseline classifier, rule-based context checks and drafts, approval workflow, guard, audit log, UI | 30-minute deploy window. The deterministic engine reads the same records the MCP tools will expose, so the Claude agent replaces it without changing the workflow. |

### What broke and how it was fixed

1. **Disk full mid-build.** A shell command failed with `ENOSPC` before it ran. Paused, the space was freed, resumed. Phase 5 (transformer fine-tuning) needs ~10-15 GB, so space is checked before that phase.
2. **Payroll row numbers drifted.** `test_injected_errors_are_observable_in_csv` failed: `DUPLICATE_ROW` inserted the copy mid-file, shifting every later row, so row numbers recorded for earlier defects pointed at the wrong employee. Fix: append duplicates at the end of the file. Lesson: when ground truth references positions, mutations must be append-only.
3. **PII ground truth listed values that were not in the text.** Template variants were Python f-strings inside `rng.choice((...))`, so every variant was evaluated and recorded its PII even though only one was used. Fix: keep only spans that appear in the rendered ticket. Caught by `test_every_pii_span_appears_in_its_ticket`.
4. **Phone notes broke names.** Phone-note rendering lowercased the first letter of the caller's statement, which turned "Kari Wilson" into "kari Wilson" and invalidated the exact-match PII span. Fix: keep the original casing.
5. **Token highlighting missed `[SSN_LAST4_1]`.** The UI regex allowed only `[A-Z_]` in token names. Fix: allow digits.

### Known limitations (deliberately carried forward)

- **Classifier scores 100% on held-out tickets.** The templated tickets are too easy to separate, so this number is not meaningful yet. Phase 5 adds paraphrased and adversarial tickets and compares TF-IDF, a fine-tuned transformer, and Claude zero-shot.
- **Name detection uses a dictionary of census names.** That is realistic for a recordkeeper (it knows the sponsor's people), but names not on file, such as a spouse or beneficiary, would be missed. NER is a planned addition.
- **Routing accuracy is 93%.** Misses come from rule-based root-cause inference (for example, telling a match true-up question apart from a funding delay). The agent's record lookups are expected to close this gap; the eval suite tracks it.
- **The demo serves HTTP on the ALB DNS name.** HTTPS needs a domain and an ACM certificate.
- **Workflow state lives in in-container SQLite** and resets when the task restarts. That is acceptable for a demo; RDS or EFS would be used in production.
