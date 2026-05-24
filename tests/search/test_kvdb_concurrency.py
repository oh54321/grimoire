from __future__ import annotations

import random
import threading

from search import KVDatabase


def test_concurrent_readers_and_writer(fake_embedder):
    db = KVDatabase(embedder=fake_embedder, initial_capacity=64)

    phrases = [f"phrase-{i}" for i in range(50)]
    for i, p in enumerate(phrases):
        db.add(p, {"version": 0, "i": i})

    ever_added: set = set()
    ever_added_lock = threading.Lock()

    def record(value):
        with ever_added_lock:
            ever_added.add((value["version"], value["i"]))

    for i in range(50):
        record({"version": 0, "i": i})

    errors: list[BaseException] = []

    def reader():
        rng = random.Random(threading.get_ident())
        try:
            for _ in range(300):
                q = rng.choice(phrases)
                results = db.search(q, n=5)
                for value, _score in results:
                    assert (value["version"], value["i"]) in ever_added
        except BaseException as e:
            errors.append(e)

    def writer():
        rng = random.Random(0xDEADBEEF)
        try:
            for n in range(100):
                p = rng.choice(phrases)
                version = n + 1
                i = phrases.index(p)
                value = {"version": version, "i": i}
                record(value)
                db.add(p, value)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], errors
    assert len(db) == 50
