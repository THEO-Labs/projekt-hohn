import argparse
from getpass import getpass

from app.auth.models import User
from app.auth.security import hash_password
from app.db import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new user")
    parser.add_argument("--email", required=True)
    args = parser.parse_args()

    pw = getpass("Password: ")
    pw2 = getpass("Repeat: ")
    if pw != pw2:
        raise SystemExit("Passwords do not match")
    if len(pw) < 8:
        raise SystemExit("Password must be at least 8 characters")

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == args.email).one_or_none()
        if existing:
            raise SystemExit(f"User {args.email} already exists")
        user = User(email=args.email, password_hash=hash_password(pw))
        db.add(user)
        db.commit()
        print(f"Created user {args.email} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
