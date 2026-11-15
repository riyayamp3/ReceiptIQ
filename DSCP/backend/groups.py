"""Groups: create, join, list, switch, roster. A user can belong to multiple groups."""
import secrets
from contextlib import closing

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import current_user
from db import connect

router = APIRouter(prefix="/api/groups")


def group_info(con, gid):
    g = con.execute("select id, name, join_code from groups where id = ?", (gid,)).fetchone()
    if not g:
        raise HTTPException(404, "Group not found")
    members = [
        dict(r)
        for r in con.execute(
            "select u.id, u.name, u.email from group_members gm "
            "join users u on u.id = gm.user_id where gm.group_id = ? order by u.id",
            (gid,),
        )
    ]
    return {"id": g["id"], "name": g["name"], "join_code": g["join_code"], "members": members}


class GroupCreate(BaseModel):
    name: str


class GroupJoin(BaseModel):
    join_code: str


class GroupSwitch(BaseModel):
    group_id: int


@router.post("")
def create_group(body: GroupCreate, user=Depends(current_user)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Please enter a group name")
    code = secrets.token_urlsafe(4)
    with closing(connect()) as con, con:
        gid = con.execute(
            "insert into groups (name, join_code, created_by) values (?, ?, ?)",
            (name, code, user["id"]),
        ).lastrowid
        con.execute("insert or ignore into group_members (group_id, user_id) values (?, ?)", (gid, user["id"]))
        con.execute("update users set active_group_id = ? where id = ?", (gid, user["id"]))
        return group_info(con, gid)


@router.post("/join")
def join_group(body: GroupJoin, user=Depends(current_user)):
    with closing(connect()) as con, con:
        g = con.execute("select * from groups where join_code = ?", (body.join_code.strip(),)).fetchone()
        if not g:
            raise HTTPException(404, "No group with that code")
        con.execute("insert or ignore into group_members (group_id, user_id) values (?, ?)", (g["id"], user["id"]))
        con.execute("update users set active_group_id = ? where id = ?", (g["id"], user["id"]))
        return group_info(con, g["id"])


@router.get("")
def list_groups(user=Depends(current_user)):
    with closing(connect()) as con:
        groups = con.execute(
            "select g.id, g.name, g.join_code from group_members gm "
            "join groups g on g.id = gm.group_id where gm.user_id = ? order by g.id",
            (user["id"],),
        ).fetchall()
        return [dict(g) for g in groups]


@router.get("/active")
def get_active_group(user=Depends(current_user)):
    gid = user.get("active_group_id")
    if not gid:
        return None
    with closing(connect()) as con:
        return group_info(con, gid)


@router.put("/active")
def switch_group(body: GroupSwitch, user=Depends(current_user)):
    with closing(connect()) as con, con:
        member = con.execute(
            "select 1 from group_members where group_id = ? and user_id = ?",
            (body.group_id, user["id"]),
        ).fetchone()
        if not member:
            raise HTTPException(403, "You are not a member of this group")
        con.execute("update users set active_group_id = ? where id = ?", (body.group_id, user["id"]))
        return group_info(con, body.group_id)


@router.get("/roster")
def roster(user=Depends(current_user)):
    gid = user.get("active_group_id")
    if not gid:
        raise HTTPException(404, "No active group")
    with closing(connect()) as con:
        return [
            dict(r)
            for r in con.execute(
                "select u.id, u.name, u.email from group_members gm "
                "join users u on u.id = gm.user_id where gm.group_id = ? order by u.id",
                (gid,),
            )
        ]
