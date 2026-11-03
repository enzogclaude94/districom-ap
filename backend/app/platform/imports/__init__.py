"""Import framework of the platform (ADR 014, docs/IMPORTS.md): original files, jobs, staging rows, readers by
format, the registry of import types and the generic pipeline upload → detect → analyze → preview → confirm.

Import types and their adapters belong to the modules, which declare them in `ModuleSpec.import_types`.
"""
