# D-0118: Retry Step Right after the response decoding fix

Recorded: 2026-09-17  
Decided by: owner, 2026-09-17; source: Owner instruction in the implementation session  
Topic: Step Right fixture acquisition  
Supersedes: The no-retry limit for failed Step Right body operation 002  
Superseded by: —

## Decision

Preserve Step Right body operation 002 as `stopped_incomplete`. Correct the
runner so already-decoded bytes are not decompressed again when HTTPX retains a
`Content-Encoding: gzip` header, then seal and independently review a new
operation before requesting the same exact body again.

The owner waived scraping limits through the end of v2 and explicitly invited
another attempt. That instruction permits a new request after operation 002
exhausted its one-request authority. The new operation still uses durable
accounting, production isolation, the operator hold, a fresh packet and a
single-use quarantine. It grants no source-kind admission, historical-year
acceptance or publication.

## Why

Operation 002 received complete decoded HTML and valid capture metadata. The
runner then passed those decoded bytes to HTTPX with the original gzip header,
which caused a second decompression and the terminal error. The retained
failure is sound evidence of that bug, but it is not a successful capture.

## Links

- [Step Right runner design](../investigations/2026/stepright-body-runner-design-2026-09-17.md)
- [Compact operation receipt](../evidence/admission/stepright-body-2026-09-17/receipt.json)
- [Standing v2 authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
