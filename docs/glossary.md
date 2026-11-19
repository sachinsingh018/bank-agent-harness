# Glossary

Finance terms, added as they come up.

- **AML (anti-money laundering):** rules and checks banks use to stop criminals moving dirty money through accounts.
- **KYB (know your business):** checking who a business customer really is and who owns it.
- **Alert:** a flag from automated rules that a transaction pattern looks suspicious. Most are false alarms.
- **Typology:** a named money laundering pattern, such as fan-in, fan-out or cycles.
- **Four-eyes principle:** two people must agree before a risky action. Here, the agent proposes and you approve.
- **Maker-checker:** the same idea as four-eyes: one party prepares, another reviews.
- **False positive:** an alert that turns out to be harmless. Most alerts are.
- **Precision (of alerts):** of the alerts raised, the share that were real laundering. Low precision means many false alarms.
- **Recall:** of the real laundering, the share that was caught.
- **Fan-out:** one account sends money to many different accounts in a short time, splitting a large sum into pieces.
- **Fan-in:** many accounts send money to one account in a short time, collecting scattered money in one place.
- **Cycle:** money leaves an account, passes through others and returns to the start, like a loop.
- **Scatter-gather:** a source splits money across middle accounts, which all pay one final account.
- **Gather-scatter:** a hub account collects money from several accounts, then pays it out to several others.
- **Bipartite (laundering pattern):** a many-to-many web where several senders pay several of the same receivers.
- **Stack (laundering pattern):** money passes quickly through a chain of accounts, hiding where it started.
- **Structuring:** making several transfers just under a reporting limit (US 10,000) to avoid being reported.
- **Layering:** moving money through many accounts to hide its origin.
- **SAR (Suspicious Activity Report):** the report a bank files with the regulator when activity looks like laundering. In this project "escalate" means the agent recommends a SAR review; a human decides.
- **Calibration:** whether a stated confidence matches reality. If the agent says 0.8 on many alerts, about 80% of them should be real laundering.
- **ECE (expected calibration error):** one number for calibration: group answers by confidence, compare each group's average confidence with how often it was right, and average the gaps. Lower is better.
- **Data leak (in evaluation):** a clue in the test data that gives away the answer for a reason that would not exist in real life, so scores look better than they are.
- **Audit log:** a record of who did what and when (agent proposals, human approvals), which nobody may change or delete afterwards. Regulators expect banks to keep one for every decision.
- **Hash chain:** a way to make tampering visible: each record stores a fingerprint (hash) of itself plus the fingerprint of the record before it. Change one old record and every fingerprint after it no longer matches.
- **False alarm:** an alert that turns out to be ordinary activity, the same thing as a false positive. Closing one saves an analyst work; closing a real case lets laundering through.
- **Escalate and close:** the two answers to an alert. Escalate means "a person should look closer, maybe file a SAR"; close means "this looks harmless, no further action".
- **Strict truth:** this project's rule for calling an alert real: at least half of its transfers must be labelled as laundering. A looser rule (any one laundering transfer) would count many more alerts as real.
- **Rules baseline:** what the monitoring rules achieve on their own, used as the yardstick for the agent. Rules escalate every alert they raise, so they never close a real case but leave every false alarm to a person.
- **Development and held-out alerts:** development alerts are the ones looked at while improving prompts; held-out alerts are kept aside and only used for a final, fairer test. Scores on development alerts can be optimistic.
