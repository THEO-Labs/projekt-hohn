import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://hohn:hohn_dev@localhost:5432/hohn_test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("COOKIE_SECURE", "false")
