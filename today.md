# Table tennis — actionable bets (TT Elite · Setka · Liga Pro · TT Cup)

_updated 2026-07-11 07:22 UTC_

Per-league shrunk-posterior rules (holdout-validated). Fewer, sharper bets.

```
Traceback (most recent call last):
  File "/home/runner/work/tt-elite/tt-elite/check_today.py", line 177, in <module>
    main()
  File "/home/runner/work/tt-elite/tt-elite/check_today.py", line 153, in main
    paper_ledger.log_flags(bets, args.line)
  File "/home/runner/work/tt-elite/tt-elite/paper_ledger.py", line 48, in log_flags
    cur = con.execute(
          ^^^^^^^^^^^^
sqlite3.OperationalError: table paper_bets has 20 columns but 15 values were supplied
(no fixtures right now)
```
