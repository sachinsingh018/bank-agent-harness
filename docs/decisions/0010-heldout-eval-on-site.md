# 0010: Show the held-out eval on the public site

**What:** `results.json` in the showcase bundle gains a `heldout` block from eval run 20261120T032954Z-00b166 (prompt v5, nvidia/nemotron-3-ultra-550b-a55b:free, reviewer agent off, 73 held-out alerts) and `"headline_source": "heldout"`. The development block from run 20261118T234719Z-cd5c6c (prompt v3, 98 development alerts) stays unchanged. The landing headline and the top of /results now use the early held-out split: of 59 alerts, the agent would recommend closing 32 of 46 false positives and 3 of 13 true positives, under strict truth with routed cases counted as escalated.

**Why:** The prompts were tuned on the development alerts, so those numbers can be optimistic. The held-out alerts were never used for tuning, so they are the fairer headline.

**Caveats shown on the site:**
- The two runs used different prompt versions (v3 on development, v5 on held-out). They are not a like-for-like comparison and the site says so.
- The numbers are small: 13 true positives in the early held-out set.
- The reviewer agent was off for the held-out run.

**Still refused:** held-out alerts are never exported as replay cases. Only aggregate counts appear. The curated replays stay development-only.

**Exporter change:** the exporter now builds showcase cards for the curated run ids directly, with the API's own helpers, instead of reading the newest 200 runs from `GET /showcase`. The 73 new held-out runs had pushed older curated runs off that list. The output for the curated runs is byte-identical to before.

**Alternatives:**
- Rerun development on prompt v5 first for a like-for-like comparison: worth doing later, but it costs a full run and the held-out result is useful now.
- Keep the development headline: it would lead with the number most likely to be optimistic.
