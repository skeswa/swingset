# D-0005: Keep research tools and evidence inside the project journal

Status: Accepted  
Recorded: 2026-09-15  
Accepted: 2026-09-15, Sandile Keswa  
Acceptance source: Owner instruction: “Please move research into journal and organize it - it feels like a hodgepodge right now”  
Topic: Documentation and research  
Supersedes: D-0001's placement of tools and evidence in top-level `research/`  
Superseded by: —

## Decision

Keep research narratives, reusable tools, and retained evidence under `journal/`.
Group tools by purpose and evidence by topic and investigation. Remove the
top-level `research/` directory. Each topic guide should connect the question,
the tools used to investigate it, and the supporting files.

## Why

Moving only the narratives left dozens of unrelated scripts and hundreds of
captures in a separate directory. A reader still had to infer which files
belonged together. Keeping them within the journal makes the research easier
to follow from a conclusion down to its evidence.

## Alternatives

- **Keep top-level research with a larger index.** This preserves paths but
  leaves the split that the owner found confusing.
- **Move everything into one journal subdirectory unchanged.** This changes
  the address without organizing the contents.
- **Rewrite old manifests to use new paths.** This invalidates their hashes
  and changes the evidence behind earlier receipts.

## Consequences

Current scripts, imports, tests, and documentation use the new locations.
Captured files keep their exact bytes. A checked path map resolves old references
inside frozen manifests and records the original hashes. Retained scripts remain
historical evidence; old receipts do not certify newly edited tools.

D-0001's reading hierarchy, writing rules, and formal decision process remain
in force. This replaces only the placement of research tools and evidence.

## Links

- [Earlier decision](0001-documentation-and-decisions.md)
- [Journal entry point](../README.md)
- [Research tools](../tools/README.md)
- [Evidence and historical paths](../evidence/README.md)
