"""Data quality issues of the platform (ADR 014 §2.11, DATA_MODEL.md §7.5): persistent, deduplicated while open,
linked to the job, the staged row and the entity. Each module registers its own codes (`ModuleSpec.quality_codes`).
"""
