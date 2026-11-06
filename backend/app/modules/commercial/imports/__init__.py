"""The STORE SYS reports Inteligencia Comercial imports (docs/IMPORTS.md §3), run by the platform's pipeline.

Each adapter reads the cells the platform's readers hand it (never a file library), proposes a canonical record per
row in `analyze` (read-only), and writes it in `write`. Customer-facing messages never carry a row value.
"""
