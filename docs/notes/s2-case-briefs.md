# S2 case briefs (local, not committed)

One page per curated run, for your approve or reject decisions. Truth comes from the dataset labels (strict rule: an alert is real laundering when at least half of its transfers are laundering). The agent never saw these labels. Suggestions are only suggestions; you decide.

## 1. Caught  (f7eb0d02)

- **Alert:** AL-0679023ed0bc, fan-out (One account paid many different accounts in a short time). Account 7675/8093BA230 sent to 8 different accounts in 8 transfers between 2022-09-06 17:24 and 2022-09-08 19:48.
- **Status:** proposed; prompt v1, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** escalate at confidence 0.85; typology FAN-OUT
- **Evidence:** 2 claims citing 8 transfers; 2 figures; 4 tool calls; 33,130 tokens; 187 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 8 of 8 alert transfers are laundering, so this is **real laundering** (strict rule)
- **Agent was:** right
- **Suggested decision:** **Approve**. The agent escalated real laundering with high confidence and its checks passed.
- **Agent's narrative:** Account 7675/8093BA230 sent 8 transfers to 8 distinct counterparties across 4 currencies (US Dollar, Euro, Rupee, Canadian Dollar) within a 50-hour window from 2022-09-06 to 2022-09-08. Each counterparty received exactly one transfer and had no prior relationship with the anchor account. The anchor account had only 1 incoming transfer in its lifetime and 8 self-transfers, but no other outgoing activity. This matches the fan-out typology: a single source splitting funds to many new recipients in a short burst, which is consistent with structuring to avoid detection.

## 2. Caught faster  (823c8309)

- **Alert:** AL-0679023ed0bc, fan-out (One account paid many different accounts in a short time). Account 7675/8093BA230 sent to 8 different accounts in 8 transfers between 2022-09-06 17:24 and 2022-09-08 19:48.
- **Status:** proposed; prompt v2, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** escalate at confidence 0.75; typology FAN-OUT
- **Evidence:** 3 claims citing 9 transfers; 5 figures; 6 tool calls; 39,296 tokens; 73 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 8 of 8 alert transfers are laundering, so this is **real laundering** (strict rule)
- **Agent was:** right
- **Suggested decision:** **Approve**. Same real laundering case, escalated again; checks passed.
- **Agent's narrative:** Account 7675/8093BA230 was opened on 2022-09-01, received a single external incoming transfer of 83.45 EUR on 2022-09-02 from account 322542/8093B9E70, and then within two days (2022-09-06 to 2022-09-08) sent 8 transfers to 8 different counterparties in 4 currencies (USD, EUR, INR, CAD). Each counterparty had no prior relationship with the account. The fan-out detector correctly flagged this burst of 8 transfers to 8 distinct accounts within a 96-hour window. The pattern — a new account with minimal funding rapidly dispersing funds to many new counterparties across multiple currencies — is con

## 3. Missed  (29c00e8b)

- **Alert:** AL-0679023ed0bc, fan-out (One account paid many different accounts in a short time). Account 7675/8093BA230 sent to 8 different accounts in 8 transfers between 2022-09-06 17:24 and 2022-09-08 19:48.
- **Status:** proposed; prompt v3, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** close at confidence 0.15; typology NONE
- **Evidence:** 4 claims citing 16 transfers; 5 figures; 7 tool calls; 35,975 tokens; 74 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 8 of 8 alert transfers are laundering, so this is **real laundering** (strict rule)
- **Agent was:** wrong
- **Suggested decision:** **Reject**. The agent proposed closing a real laundering case. Rejecting shows the analyst catching the miss.
- **Agent's narrative:** The fan-out alert on account 7675/8093BA230 reflects a legitimate multi-currency payment operation. The account converts a Euro balance into USD, CAD, and INR via same-timestamp self-transfers, then sends each converted amount to a distinct counterparty. All 8 counterparties appear only once, consistent with one-time payments to international suppliers. The only incoming funds are a trivial 83.45 EUR cash deposit; the large Euro amounts used for conversion (totalling ~94,484 EUR) were pre-existing. No layering, return flows, or structuring are present. The fan-out shape is fully explained by o

## 4. Sent to a person  (e75bcdb8)

- **Alert:** AL-0679023ed0bc, fan-out (One account paid many different accounts in a short time). Account 7675/8093BA230 sent to 8 different accounts in 8 transfers between 2022-09-06 17:24 and 2022-09-08 19:48.
- **Status:** human_review (reviewer_failed); prompt v5, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** close at confidence 0.15; typology NONE
- **Evidence:** 3 claims citing 16 transfers; 1 figures; 6 tool calls; 78,448 tokens; 1631 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** reviewer agent did not finish (limit_time)
- **Truth:** 8 of 8 alert transfers are laundering, so this is **real laundering** (strict rule)
- **Agent was:** wrong
- **Suggested decision:** **Reject**. The agent proposed closing real laundering and the reviewer agent timed out. Rejecting shows the safety net working.
- **Agent's narrative:** The anchor account 7675/8093BA230 sent 8 ACH payments to 8 different counterparties over a 50-hour window in 6 currencies (USD, EUR, INR, CAD), triggering the fan-out detector (threshold: 6 counterparties in 96 hours). The account's history shows only one incoming external transfer (EUR 83.45 cash) and 8 self-transfers that are currency conversions (EUR to USD, INR, CAD) paired with each outbound payment. Each counterparty received exactly one payment from this anchor and has independent transaction histories with many other parties. The amounts vary widely (USD 1,338 to CAD 22,304) and are no

## 5. Caught  (029a419f)

- **Alert:** AL-833c795ec18e, scatter-gather (Money was split across several accounts, then gathered back together). Account 7675/8093BA230 sent money through 8 middle accounts that all paid account 210261/803B8BFC0, in 18 transfers between 2022-09-06 17:24 and 2022-09-10 15:43.
- **Status:** proposed; prompt v3, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** escalate at confidence 0.9; typology SCATTER-GATHER
- **Evidence:** 3 claims citing 18 transfers; 7 figures; 7 tool calls; 19,436 tokens; 51 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 16 of 18 alert transfers are laundering, so this is **real laundering** (strict rule)
- **Agent was:** right
- **Suggested decision:** **Approve**. Escalated real laundering with high confidence.
- **Agent's narrative:** Alert AL-833c795ec18e shows a textbook scatter-gather pattern. Source account 7675/8093BA230 sent funds to 8 distinct middle accounts between 2022-09-06 and 2022-09-08, using 5 different currencies (USD, EUR, INR, CAD, and one more USD). Each middle account then paid the same final account 210261/803B8BFC0 in JPY within 1-2 days, totalling ~10.49 million JPY across 10 transfers. The middle accounts acted as pure pass-throughs: each received once from the source and sent once (or twice) to the final account, converting to JPY. No ordinary business purpose explains dispersing funds to 8 accounts

## 6. Caught  (20be587f)

- **Alert:** AL-986d0280c6ee, fan-in (Many different accounts paid one account in a short time). Account 21749/801F05EF0 received from 10 different accounts in 10 transfers between 2022-09-05 18:12 and 2022-09-09 10:44.
- **Status:** proposed; prompt v3, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** escalate at confidence 0.75; typology GATHER-SCATTER
- **Evidence:** 3 claims citing 14 transfers; 3 figures; 7 tool calls; 45,022 tokens; 60 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 10 of 10 alert transfers are laundering, so this is **real laundering** (strict rule)
- **Agent was:** right
- **Suggested decision:** **Approve**. Escalated real laundering; every transfer in the alert is laundering.
- **Agent's narrative:** Alert AL-986d0280c6ee fired on a fan-in pattern: account 21749/801F05EF0 received from 10 different accounts in 10 transfers over 96 hours. The same account also matches a gather-scatter pattern (run_pattern_check): it gathered from 10 distinct senders then scattered to 4 distinct receivers in 14 transfers over 8 days. The 10 incoming transfers (all ACH, all one-time counterparties) total 63.0M EUR, dominated by a single 62.9M EUR transfer from 800F2EAC0. Three of those senders (8001DB9C0, 801ADF5B0, 800AA6350) themselves exhibit gather-scatter behaviour. The hub account is new (first activity

## 7. False alert closed  (70353513)

- **Alert:** AL-01cf1e794cc7, cycle (Money went round in a circle and came back to where it started). Money went around a loop of 2 accounts that includes 50528/812D0C600 in 4 transfers between 2022-09-03 02:36 and 2022-09-08 12:57.
- **Status:** proposed; prompt v3, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** close at confidence 0.15; typology NONE
- **Evidence:** 3 claims citing 23 transfers; 1 figures; 8 tool calls; 40,543 tokens; 50 s
- **Checks:** citations passed; arithmetic passed; alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 0 of 4 alert transfers are laundering, so this is **a false alert** (strict rule)
- **Agent was:** right
- **Suggested decision:** **Approve**. Proposed closing an alert with no laundering transfers at all: correct.
- **Agent's narrative:** The cycle alert fired on four ACH transfers between accounts 50528/812D0C600 and 148350/812D0C3C0 over six days. Both accounts show ordinary business activity: 812D0C600 receives regular payroll-like deposits from 223/811A83210 (cheques of 1,244.94 and card payments of 727.89 recurring multiple times per week) and a large cheque deposit of 255,267.17 from 149340/812838660. Both accounts transact heavily with 148350/811FFF630 (large ACH flows in both directions). The two alerted accounts share this common counterparty and also settle balances with each other. The four cycle transfers (8677, 103

## 8. Checks sent it to a person  (61e56e79)

- **Alert:** AL-3332102d5f16, gather-scatter (Money pooled in one account, then was spread out again). Account 10709/80692A790 received from 4 different accounts and paid 3 different accounts in 31 transfers between 2022-09-02 06:04 and 2022-09-10 03:01.
- **Status:** human_review (verifier_failed); prompt v1, model nvidia/nemotron-3-ultra-550b-a55b:free
- **Agent recommends:** escalate at confidence 0.85; typology GATHER-SCATTER
- **Evidence:** 5 claims citing 31 transfers; 5 figures; 10 tool calls; 55,840 tokens; 200 s
- **Checks:** citations passed; arithmetic FAILED (figure 2: sum does not equal the exact sum of the cited transfers on the paid side, figure 3: sum does not equal the exact sum of the cited transfers on the pai); alert_membership passed
- **Reviewer:** no reviewer agent ran
- **Truth:** 2 of 31 alert transfers are laundering, so this is **a false alert** (strict rule)
- **Agent was:** wrong
- **Suggested decision:** **Reject**. The agent escalated, but its arithmetic check failed and only 2 of 31 transfers are laundering, below the strict threshold of half. Rejecting overturns an unneeded escalation.
- **Agent's narrative:** Account 10709/80692A790 acted as a hub receiving funds from 4 source accounts and sending to 3 destination accounts over an 8-day window. All 4 sources (25536/80313A000, 22806/801B12970, 111121/80C2C7140, 17222/8041EDC70) have zero other counterparties and only send to this hub. All 3 destinations (225690/80FFF9840, 121415/80BA7C7B0, 18819/805B9B260) have zero other counterparties and only receive from this hub. The hub itself was active for only 10 days (first seen Sep 1, last seen Sep 10). Source 80313A000 sent identical large amounts twice (€5,832.55 and €25,861.50 on Sep 2 and Sep 9). Sour
