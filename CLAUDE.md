# CLAUDE.md

@AGENTS.md

@GUIDELINES_QUERYSPY.md

The imported guidelines are binding. Three always-on rules:

- Public SQLAlchemy event API only — never monkeypatch ORM internals.
- `lazy_loaded_from`, never `is_relationship_load`, is the lazy-load discriminator.
- Mutation testing is a local, occasional, targeted audit — never wire it into CI.
