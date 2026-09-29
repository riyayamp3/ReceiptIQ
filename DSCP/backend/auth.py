"""Accounts: sign up, log in/out, profile. Passwords use salted scrypt; sessions are random tokens in an httpOnly cookie."""
import hashlib
import hmac
import re
import secrets
from contextlib import closing

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from db import connect

router = APIRouter(prefix="/api")
SESSION_DAYS = 30
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password, stored):
    _, salt, _ = stored.split("$")
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)), stored)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public(user):
    return {k: user[k] for k in ("id", "name", "email", "monthly_budget")}


def current_user(session: str | None = Cookie(default=None)):
    """FastAPI dependency: the logged-in user, or 401."""
    if session:
        with closing(connect()) as con:
            row = con.execute(
                """select u.* from sessions s join users u on u.id = s.user_id
                   where s.token_hash = ? and s.expires_at > datetime('now')""",
                (token_hash(session),),
            ).fetchone()
        if row:
            return dict(row)
    raise HTTPException(401, "Please log in")


def start_session(response, user_id):
    token = secrets.token_urlsafe(32)
    with closing(connect()) as con, con:
        con.execute("delete from sessions where expires_at <= datetime('now')")
        con.execute(
            f"insert into sessions (token_hash, user_id, expires_at) values (?, ?, datetime('now', '+{SESSION_DAYS} days'))",
            (token_hash(token), user_id),
        )
    # ponytail: secure=False so it works on http://localhost; set secure=True when served over HTTPS
    response.set_cookie("session", token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")


class Signup(BaseModel):
    name: str
    email: str
    password: str


class Login(BaseModel):
    email: str
    password: str


class Profile(BaseModel):
    name: str
    monthly_budget: float | None = None


@router.post("/auth/signup")
def signup(body: Signup, response: Response):
    name, email = body.name.strip(), body.email.strip().lower()
    if not name:
        raise HTTPException(400, "Please enter your name")
    if not EMAIL.match(email):
        raise HTTPException(400, "Please enter a valid email")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    with closing(connect()) as con, con:
        if con.execute("select 1 from users where email = ?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        first = not con.execute("select 1 from users").fetchone()
        uid = con.execute(
            "insert into users (name, email, password_hash) values (?, ?, ?)", (name, email, hash_password(body.password))
        ).lastrowid
        if first:  # receipts uploaded before accounts existed belong to the first account
            con.execute("update receipts set user_id = ? where user_id is null", (uid,))
            con.execute("update images set user_id = ? where user_id is null", (uid,))
        user = con.execute("select * from users where id = ?", (uid,)).fetchone()
    start_session(response, uid)
    return public(user)


@router.post("/auth/login")
def login(body: Login, response: Response):
    # ponytail: no rate limiting on failed logins; add it before exposing the app publicly
    with closing(connect()) as con:
        user = con.execute("select * from users where email = ?", (body.email.strip().lower(),)).fetchone()
    if not user or not check_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    start_session(response, user["id"])
    return public(user)


@router.post("/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        with closing(connect()) as con, con:
            con.execute("delete from sessions where token_hash = ?", (token_hash(session),))
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/me")
def me(user=Depends(current_user)):
    return public(user)


@router.put("/me")
def update_me(body: Profile, user=Depends(current_user)):
    if not body.name.strip():
        raise HTTPException(400, "Please enter your name")
    if body.monthly_budget is not None and body.monthly_budget < 0:
        raise HTTPException(400, "Budget can't be negative")
    with closing(connect()) as con, con:
        con.execute("update users set name = ?, monthly_budget = ? where id = ?", (body.name.strip(), body.monthly_budget, user["id"]))
        return public(con.execute("select * from users where id = ?", (user["id"],)).fetchone())


if __name__ == "__main__":
    stored = hash_password("correct horse")
    assert check_password("correct horse", stored) and not check_password("wrong horse", stored)
    assert hash_password("x") != hash_password("x")  # salted
    print("auth ok")
