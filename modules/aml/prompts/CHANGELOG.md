# AML investigator prompt changelog

- v1: first investigator prompt (typologies, how to work, evidence rules, decision rules, 12 tool call budget).
- v2: adds a step to compute every sum figure with the new sum_transfers tool and states the answer limits plainly (1 to 50 transfer ids and at most 20 amounts per claim, at most 10 claims and 10 figures, narrative at most 1,200 characters). Why: in run 20261116T014628Z-c9d334 one case stated wrong sums that the arithmetic verifier caught and two first submissions broke the schema caps.
