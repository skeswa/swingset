# D-0071: Retain undated map markers as review gaps

Recorded: 2026-09-17  
Decided by: agent  
Topic: Historical calendar evidence  
Supersedes: —  
Superseded by: —

## Decision

Retain complete controls for the four acquired 2016 map bodies and account for
their failed date interpretation explicitly. Do not create dated occurrences
from capture timestamps or infer a year from an event name. This inspection
changes no parser, production finding or year acceptance.

## Why

The four bodies contain 59, 51, 112 and 126 marker calls. Every marker's printed
popup consists only of an event-name anchor; none supplies a date line. The
existing parse failure therefore reflects missing source dates, rather than a
missing body. Each page also contains one commented example call, which must
not become an event if the map parser is extended later.

Names and websites remain useful review evidence. Associating them with dated
calendar occurrences requires independent matching evidence and explicit
handling of ambiguity. See [the retained-body inspection](../investigations/2026/calendar-map-gaps-2026-09-17.md).
