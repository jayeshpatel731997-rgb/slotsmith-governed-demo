# Vendored runtime wheels

These CPython 3.12 / manylinux x86-64 wheels are committed solely so the
portfolio demo can build without network access. `SHA256SUMS` is verified in
the Docker build before installation. Refresh `runtime/` deliberately when the two
top-level runtime pins in `pyproject.toml` change, then run the full test,
license, vulnerability, and offline-image gates.
