"""Benchmark harness for Third Brain: retrieval quality, answer quality, and
single-request performance, measured by driving the real services.

Submodules are imported explicitly (``from benchmarks.metrics import ...``). This
package ``__init__`` deliberately imports nothing, so that lightweight modules such
as :mod:`benchmarks.metrics` never pull in the database or application layer.
"""
