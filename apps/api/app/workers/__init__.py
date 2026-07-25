"""Background worker package (arq).

Kept import-light on purpose: importing this package (which happens whenever a
submodule such as ``app.workers.queue`` is imported by the API) must NOT require arq
to be installed. Heavy/optional imports live inside the submodules that need them
(``settings`` and ``tasks`` run only inside the worker process).
"""
