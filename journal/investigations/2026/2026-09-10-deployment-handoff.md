# Initial deployment handoff

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

The selected writer is the OrbStack machine `swingset`. The
`swingset-restore` machine passed recovery of both the original flat checkpoint
and the packed checkpoint, then was stopped. It has no collection timers.
Keep exactly one writer active. The token stays in `/etc/swingset.env`, owned
by root with mode 0600, outside the checkout and checkpoint.

The initial published calendar head is
`957b9e266b549d729ef978b3d0ddcc738c7d901f`. Recovery evidence is in GitHub issue
#8; the packed archive used was
`c691a760015698247d157c2c2cf738fc712ea53e`. The recovery targets were
`/var/lib/swingset/recovered` and `/var/lib/swingset/recovered-packed`, separate
from the CLI environment and caches. Always pass the selected target with
`--state` to restore, doctor, and the verification dry cycle.

Historical receipt: the initial results publication used installed revision
`8fcc0a2564a635ea78ab69c01d9e2055233b4e23`, candidate
`cand_984c84cc9dd34af8`, and public commit
`a4abf85ff6e6e0ad4dd2088e130668987856613a`. Candidate review checked all 17
schemas, file hashes, provenance, unique keys and dataset-card counts. Public
verification matched the reviewed manifest and a remote DuckDB query returned
3,368 placement rows. The published surface contains 312 contests, 255 events,
12,397 entries and 498 rounds. The Hub reports all 17 splits ready, and events, placements, entries, rounds
and final-marks previews each returned HTTP 200 with 100 rows. The private
results-state backup completed at 15:37:05 UTC at archive commit
`c716bf7f7e8eed8369828c26e2a2434f3c08aaf0`.

The production registry comparison dump is archived under SHA-256
`ae7f2b9d688b69b49d08dfc718f60e5ef6b4b6561054d2503e8c94b8ae5e1d53`, and
sweep seed 1 is installed. Initial collection fetched eight higher-priority
finalist refreshes using the normal five-second floor. Their build exposed a
registry vocabulary mismatch; the repair preserves skill and age categories
and highest-level fields. The full sweep and saved-dump comparison remain
tracked in issue #10.

The host now enables publication (`dryRun = false`). Normal collection is
every 15 minutes with up to 120 seconds of jitter; backup is 04:00 UTC Monday
through Thursday and 04:00/12:00/20:00 UTC Friday through Sunday. Summary runs
at 08:00 UTC. Check GitHub issue #9 for scheduled-run acceptance, and #1 for
overall acceptance. Issues #12–#14 track the remaining source
observations.

The active corrected release is
`2089803d379ef4233ee45a49193ce97befcf0911`. Its reviewed public candidate is
`cand_d06d6d9e48fd41a2` at commit
`ec6b7bbb84be9e5252d55c6bb1eae3feef05dd5b`. The public data has 573 events,
96 with results, 13,922 placements, 57,505 entries, 3,229 dancers and 33,427
registry placements. It includes 1,269 registry event mappings and 9,471
entries with a WSDC ID.

Private backup `556e04b59b8404a3e5a12f91c4804eecbf8fee2f` passed verification:
9,240 files and the 691,200,000-byte transport hash and size matched, as did the
public manifest and `PUBLISHED` receipt. Doctor showed no pending work or
candidates, pauses, or restore marker before resumption. All three review guards
were removed and timers resumed at 2026-09-11 03:29:39 UTC. At handoff the cycle
service was running, while backup and summary were waiting for 04:00 and 08:00
UTC. Do not count the running cycle as completed observation evidence.

The registry mirror remains partial at cursor 3,297 against the archived
27,039-ID dump. Keep issues #10, #12, #13 and #14 open; issue #15 records the
completed correction release. See the
[2026-09-10 data-quality repair report](data-quality-repair-2026-09-10.md).

The prior release `9bade45dd5d0c96264aaf463044df4f4aa4700fa`, public
commit `c9789bad676e64424aa9ab7fc581b9aefe16868c`, candidate
`cand_b2c68aaa3b7b4f3c`, and checkpoint
`245f6a4f507581254c989ee20aa7529f0f506a5b` are historical receipts. Their
cursor-2 and active-timer observations do not describe the current handoff.
