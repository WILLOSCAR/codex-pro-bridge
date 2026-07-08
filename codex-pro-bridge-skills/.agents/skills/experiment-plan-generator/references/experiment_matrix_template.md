# Experiment Matrix Template

| Priority | ID | Experiment | Change | Hypothesis tested | Code/config change | Main metric | Guardrails | Success signal | Failure interpretation | Cost | Command |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P0 | E0 | Current baseline | none | establishes reference | none | | | | | | |
| P0 | E1 | Sanity check | tiny/debug split | pipeline correctness | | | | | | low | |
| P1 | E2 | Data-only | new data, old objective | data drives gain | | | | | | medium | |
| P1 | E3 | Objective-only | old data, new objective | objective drives gain | | | | | | medium | |
| P1 | E4 | Full method | new data + new objective | full hypothesis | | | | | | high | |
| P2 | E5 | Kill experiment | remove core component | core component necessary | | | | | | medium | |

Decision rule:

- Continue if: <criteria>
- Iterate if: <criteria>
- Stop if: <criteria>
