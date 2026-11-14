# 0001: SQLAlchemy 2.0 for data access

- What: use SQLAlchemy 2.0 (with the psycopg driver) and Alembic migrations.
- Why: Alembic is built around SQLAlchemy models, typing support is good, and one consistent layer keeps the harness simple.
- Alternatives: plain psycopg (less abstraction, but hand-written migrations and more boilerplate).
