# S2 decision notes (ready to paste, local, not committed)

Open each case at http://localhost:3000/queue/<run id> (or the forwarded port 3000 link), choose the decision, paste the note, leave the lesson box empty, submit.

## 1. f7eb0d02-e996-4921-9278-1762e0e5ed03: Approve
Correct escalation of real laundering. All checks passed and the narrative matches the alert.

## 2. 823c8309-3f1d-465e-8b70-5ffb4fe19909: Approve
Correct escalation and checks passed. Wording slip: the narrative says the burst was within a 96-hour window, but 96 hours is the detector threshold; the actual burst lasted about 50 hours.

## 3. 29c00e8b-abe7-4fd9-80a1-1f920c492ab0: Reject
Closed real laundering. The narrative undercuts the close: about 94,484 EUR went out against only 83.45 EUR coming in, and the gap is explained away as a pre-existing balance. That gap is a red flag, not a reason to close.

## 4. e75bcdb8-6779-4261-afed-3e70f759a2ff: Reject
Closed real laundering, and the reviewer agent timed out after 1,631 seconds. The narrative also contradicts itself: it says 6 currencies and then lists 4.

## 5. 029a419f-6e72-4ef8-85da-25a6fe1d5e8e: Approve
Correct high-confidence escalation of a clear scatter-gather pattern. Narrative error to fix before relying on it: it says 5 different currencies but lists USD twice, so there are 4.

## 6. 20be587f-cbd0-4b2c-ad74-fbee69513bb5: Approve
Every alert transfer is laundering. Relabelling the fan-in alert as gather-scatter is reasonable because the hub also paid out to 4 accounts. The single 62.9M EUR inbound transfer should be flagged in the case notes.

## 7. 70353513-1255-4b6f-aba7-267c3402dd6e: Approve
Correctly closed an alert with no laundering transfers. The reasoning (recurring payroll-like deposits, a shared counterparty, two-way settlement) is grounded in account history, not just the alert window.

## 8. 61e56e79-9dd9-4115-8172-36e7775ddc24: Reject
The arithmetic check failed on two figures, and only 2 of 31 transfers are laundering, so under the strict rule this escalation should not stand. Caveat: those 2 laundering transfers are real; in a live setting I would reject the escalation as framed but record them rather than treat the account as fully clean.
