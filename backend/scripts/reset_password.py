"""Out-of-band password reset for a locked-out user.

Trust model: this script must be run inside the backend container
(or with the same DATABASE_URL configured), so the caller already has
shell access to the host. We deliberately do NOT expose this on the
HTTP surface — there is no email/SMS verification path in the PoC.

Usage::

    docker compose exec backend python scripts/reset_password.py \\
        --email user@example.com --password 'new-password-here'

If ``--password`` is omitted the script prompts twice (and never echoes).
The new password is checked against the same minimum-strength policy as
``/auth/register`` (>= 8 chars).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.config import Settings
from app.infra.auth import create_default_hasher
from app.infra.auth.jwt_codec import JwtCodec
from app.infra.db.factory import make_engine, make_sessionmaker
from app.infra.repositories import UserRepo
from app.services.auth import AuthService, InvalidCredentialsError
from app.services.auth.service import WeakPasswordError


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reset_password.py",
        description="Reset a MindEngine user's password out-of-band.",
    )
    p.add_argument("--email", required=True, help="email of the user to reset")
    p.add_argument(
        "--password",
        default=None,
        help="new password (omit to be prompted twice)",
    )
    return p.parse_args()


def _prompt_password() -> str:
    while True:
        first = getpass.getpass("New password: ")
        second = getpass.getpass("Confirm new password: ")
        if first != second:
            print("Passwords don't match — try again.", file=sys.stderr)
            continue
        return first


async def _reset(email: str, new_password: str) -> str:
    settings = Settings()
    engine = make_engine(settings.database_url, kind="web")
    Session = make_sessionmaker(engine)

    try:
        async with Session() as session:
            service = AuthService(
                users=UserRepo(session=session),
                hasher=create_default_hasher(),
                # JWT not used for reset, but AuthService requires it.
                jwt=JwtCodec(secret=settings.jwt_secret),
            )
            user_id = await service.admin_reset_password(
                email=email, new_password=new_password
            )
            await session.commit()
            return user_id
    finally:
        await engine.dispose()


def main() -> int:
    args = _parse_args()
    new_password = args.password or _prompt_password()

    try:
        user_id = asyncio.run(_reset(args.email, new_password))
    except InvalidCredentialsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except WeakPasswordError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 4

    print(f"OK — password updated for {args.email} (user_id={user_id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
