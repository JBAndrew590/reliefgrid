# Query benchmark method

Run `make benchmark` on a disposable local database. The script inserts 56,500 synthetic operational rows inside a single transaction (2,500 locations, 30,000 lots, 12,000 requests, 12,000 audit rows), runs `ANALYZE`, temporarily drops each target index, captures `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, recreates the index and measures again. Finally, it rolls the entire transaction back. The output includes the full plan and actual timing; do not infer production performance from one local run.

| Query | Index under test | Expected mechanism to inspect |
| --- | --- | --- |
| Nearest locations within 10 km | GiST `locations_position_gix` | Spatial filtering and KNN ordering |
| Critical open requests by deadline | Partial composite `requests_queue_idx` | Organization/priority/deadline queue scan |
| Soon-expiring available lots | Partial FEFO `lots_fefo_idx` | Resource/expiry range and order |

Compare actual root/child plan nodes, shared hit/read blocks and execution time. The second measurement may benefit from a warmed buffer cache. Keep the raw JSON from a real run alongside hardware and PostgreSQL versions if publishing numbers. This repository does not contain fabricated results.
