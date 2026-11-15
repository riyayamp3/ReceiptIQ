"""Family Group: create, join, and roster endpoints. Each user belongs to at most one family group."""
import secrets
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/family")


def family_info(con, fgid):
    """Return {id, name, join_code, members} for the given family group id."""
    fg = con.execute("select id, name, join_code from family_groups where id = ?", (fgid,)).fetchone()
    if not fg:
        raise HTTPException(404, "Family group not found")
    members = [
        dict(r)
        for r in con.execute(
            "select id, name, email from users where family_group_id = ? order by id", (fgid,)
        )
    ]
    return {"id": fg["id"], "name": fg["name"], "join_code": fg["join_code"], "members": members}


class FamilyCreate(BaseModel):
    name: str


class FamilyJoin(BaseModel):
    join_code: str


@router.post("")
def create_family(body: FamilyCreate, user=Depends(current_user)):
    """Create a new family group. The requesting user becomes the first member.
    Rejects with 409 if the user already belongs to a family group (Req 1.2)."""
    if user["family_group_id"] is not None:
        raise HTTPException(409, "You are already in a family group")
    name = body.name.strip() or f"{user['name']}'s family"
    code = secrets.token_urlsafe(4)
    with closing(connect()) as con, con:
        fgid = con.execute(
            "insert into family_groups (name, join_code, created_by) values (?, ?, ?)",
            (name, code, user["id"]),
        ).lastrowid
        con.execute("update users set family_group_id = ? where id = ?", (fgid, user["id"]))
        return family_info(con, fgid)


@router.post("/join")
def join_family(body: FamilyJoin, user=Depends(current_user)):
    """Join an existing family group by its join code (Req 1.3).
    Rejects with 404 if the code is unknown, 409 if the user already belongs to any group."""
    with closing(connect()) as con, con:
        fg = con.execute(
            "select * from family_groups where join_code = ?", (body.join_code.strip(),)
        ).fetchone()
        if not fg:
            raise HTTPException(404, "No family group with that code")
        if user["family_group_id"] is not None:
            if user["family_group_id"] == fg["id"]:
                raise HTTPException(409, "You are already a member of this family group")
            raise HTTPException(409, "You are already in a family group")
        con.execute("update users set family_group_id = ? where id = ?", (fg["id"], user["id"]))
        return family_info(con, fg["id"])



@router.get("")
def get_family(user=Depends(current_user)):
    """Return the requesting user's family group info, or null if they have none."""
    if not user["family_group_id"]:
        return None
    with closing(connect()) as con:
        return family_info(con, user["family_group_id"])


@router.get("/roster")
def roster(user=Depends(current_user)):
    """Return the list of all members in the requesting user's family group (Req 1.5).
    Returns 404 if the user has no family group."""
    if not user["family_group_id"]:
        raise HTTPException(404, "You are not in a family group")
    with closing(connect()) as con:
        return [
            dict(r)
            for r in con.execute(
                "select id, name, email from users where family_group_id = ? order by id",
                (user["family_group_id"],),
            )
        ]
