# 0006: Late dates in the AML slice give away laundering

**What:** In the slice, normal traffic almost stops after 2022-09-10. Of the 203 transfers dated 11 to 17 September, 126 are laundering (62%). Before that, 396 of 25,290 transfers are laundering (about 1.6%). Transfer ids follow time order, so late ids carry the same clue. The IBM generator lets laundering schemes run past the end of the normal sample, so this is an artifact of the synthetic data, not something a real bank would see.

**Why it matters:** an agent, a tuned prompt or a stored correction could learn "late date means laundering" and score well for the wrong reason.

**Decision:**
- Prompts never mention the date range or this effect. No prompt tuning may use it.
- Every agent metric is reported twice: alerts whose window ends on or before 2022-09-10 (the honest headline) and later alerts.
- Corrections memory (M3) must not store date based rules.

**Alternatives:**
- Drop transfers after 2022-09-10: loses 126 of 522 laundering transfers and breaks the published v1 baseline.
- Shift or shuffle dates: breaks the time windows the detectors and tools rely on.
- Ignore it: scores would be inflated without anyone noticing.

Found while verifying docs/plans/agent-loop.md (section 12).
