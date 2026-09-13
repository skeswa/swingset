# WDR judge continuity fixtures

These six complete `routeInfo.json` bodies were fetched through the project
gate on 2026-09-09 UTC and retained in the V1 published checkpoint. Each JSON
sidecar records its original URL, snapshot ID, fetch time, byte count and SHA-256.
Copying these existing bodies for the regression required no source request.

The P18 projection withheld unsupported numeric and Solo scoring contests before
collecting their named judges. Eleven named records consequently disappeared
from six events although their typed judge headers remained in retained source
evidence. `expected_preserved_judges` names the exact V3 public records affected.

The regression parses each complete body with the WDR adapter and projects it
in memory. Those eleven names must remain with null WSDC IDs and resolvable
source bindings. Unsupported contests still emit no rounds, entries or score
rows; supported ordinal contests in the same bodies still project normally.
