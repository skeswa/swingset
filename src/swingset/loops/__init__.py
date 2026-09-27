"""Five loops that share the state under a reader-writer lock (D-0215).

`runtime.tick` is the frame every loop runs in; each loop module has one
`run(tick)` function.
"""
