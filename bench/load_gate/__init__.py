"""The load gate: a repeatable, commit-pinned mixed load of people and runners against one Tico server over HTTP, whose
report tells the server's limits from the sender's own lag (bench/load_gate/README.md).

    python -m bench.load_gate run --seed 7                    # server and sender on this machine, separate processes
    python -m bench.load_gate serve --seed 7 --listen 0.0.0.0:8800   # on the server host
    python -m bench.load_gate send --target http://server:8800 --manifest manifest.json   # on the sender host

Everything it builds is fictional (Acme, example.test), from the seed. It never contacts anything but the target it
is given."""
