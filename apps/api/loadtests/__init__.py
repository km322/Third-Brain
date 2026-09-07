"""Load-testing tools for Third Brain: bulk corpus seeder + locust scenarios.

``seed_corpus`` (and this package import) must never require locust; only
``locustfile`` and ``run_load_test`` may import it, so the seeder runs with the
core API dependencies alone.
"""
