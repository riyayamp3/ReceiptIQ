"""Seed multi-group demo data. Run AFTER: python synth.py --load

Creates multiple groups per user so the multi-group feature can be demoed:
  - Sharma Family (Rajesh, Sunita, Arjun, Priya)
  - Office Lunch (Rajesh, Vikram, Neha)
  - College Friends (Aarav student, Riya, Karan)
  - Goa Trip 2026 (Sunita, Priya daughter, Neha)
"""
import secrets, sqlite3, os
from pathlib import Path
from contextlib import closing

os.chdir(Path(__file__).parent)
from auth import hash_password
from db import connect, migrate

DEMO_PASSWORD = "demo12345"

def run():
    migrate()
    with closing(connect()) as con, con:
        # Clean up any extra demo users from previous runs
        for email in ["vikram@demo.receiptiq.in", "neha@demo.receiptiq.in",
                      "riya@demo.receiptiq.in", "karan@demo.receiptiq.in"]:
            old = con.execute("select id from users where email = ?", (email,)).fetchone()
            if old:
                con.execute("delete from group_members where user_id = ?", (old["id"],))
                con.execute("delete from users where id = ?", (old["id"],))

        # Clean up extra groups (keep the ones created by synth.py --load)
        for name in ["Office Lunch", "College Friends", "Goa Trip 2026"]:
            con.execute("delete from group_members where group_id in (select id from groups where name = ?)", (name,))
            con.execute("delete from groups where name = ?", (name,))

        # Helper to create a user
        def make_user(name, email):
            old = con.execute("select id from users where email = ?", (email,)).fetchone()
            if old:
                return old["id"]
            return con.execute(
                "insert into users (name, email, password_hash, monthly_budget) values (?, ?, ?, ?)",
                (name, email, hash_password(DEMO_PASSWORD), 15000),
            ).lastrowid

        # Helper to create a group
        def make_group(name, creator_id):
            code = secrets.token_urlsafe(4)
            gid = con.execute(
                "insert into groups (name, join_code, created_by) values (?, ?, ?)",
                (name, code, creator_id),
            ).lastrowid
            return gid

        # Helper to add member to group
        def add_member(gid, uid):
            con.execute("insert or ignore into group_members (group_id, user_id) values (?, ?)", (gid, uid))

        # Get existing users
        rajesh = con.execute("select id from users where email = 'rajesh@demo.receiptiq.in'").fetchone()
        sunita = con.execute("select id from users where email = 'sunita@demo.receiptiq.in'").fetchone()
        arjun = con.execute("select id from users where email = 'arjun@demo.receiptiq.in'").fetchone()
        priya_d = con.execute("select id from users where email = 'priya.sharma@demo.receiptiq.in'").fetchone()
        aarav = con.execute("select id from users where email = 'student@demo.receiptiq.in'").fetchone()
        priya_m = con.execute("select id from users where email = 'professional@demo.receiptiq.in'").fetchone()

        # Create extra users for other groups
        vikram = make_user("Vikram Patel", "vikram@demo.receiptiq.in")
        neha = make_user("Neha Gupta", "neha@demo.receiptiq.in")
        riya = make_user("Riya Singh", "riya@demo.receiptiq.in")
        karan = make_user("Karan Mehta", "karan@demo.receiptiq.in")

        # Group 1: Sharma Family (already exists from synth.py, just ensure group_members)
        sharma_fg = con.execute("select id from groups where name = 'Sharma Family'").fetchone()
        if sharma_fg:
            for uid in [rajesh, sunita, arjun, priya_d]:
                if uid:
                    add_member(sharma_fg["id"], uid["id"])
            # Set active group for family members
            for uid in [rajesh, sunita, arjun, priya_d]:
                if uid:
                    con.execute("update users set active_group_id = ? where id = ?", (sharma_fg["id"], uid["id"]))

        # Group 2: Office Lunch Buddies (Rajesh, Vikram, Neha)
        if rajesh:
            g2 = make_group("Office Lunch Buddies", rajesh["id"])
            add_member(g2, rajesh["id"])
            add_member(g2, vikram)
            add_member(g2, neha)

        # Group 3: College Friends (Aarav/student, Riya, Karan)
        if aarav:
            g3 = make_group("College Friends", aarav["id"])
            add_member(g3, aarav["id"])
            add_member(g3, riya)
            add_member(g3, karan)
            # Set as active for student
            con.execute("update users set active_group_id = ? where id = ?", (g3, aarav["id"]))

        # Group 4: Goa Trip 2026 (Sunita, Priya daughter, Neha)
        if sunita:
            g4 = make_group("Goa Trip 2026", sunita["id"])
            add_member(g4, sunita["id"])
            if priya_d:
                add_member(g4, priya_d["id"])
            add_member(g4, neha)

        # Ensure professional has her own group as active
        if priya_m:
            solo = con.execute("select id from groups where name = \"Priya Menon's group\"").fetchone()
            if not solo:
                solo_id = make_group("My Personal", priya_m["id"])
                add_member(solo_id, priya_m["id"])
                con.execute("update users set active_group_id = ? where id = ?", (solo_id, priya_m["id"]))

        # Print summary
        print("\n=== Demo Groups ===")
        groups = con.execute("select id, name, join_code from groups order by id").fetchall()
        for g in groups:
            members = con.execute(
                "select u.name, u.email from group_members gm join users u on u.id = gm.user_id where gm.group_id = ? order by u.id",
                (g["id"],)
            ).fetchall()
            print(f"\n  {g['name']} (code: {g['join_code']})")
            for m in members:
                active = con.execute("select active_group_id from users where id in (select user_id from group_members where group_id = ? and user_id = (select id from users where email = ?))", (g["id"], m["email"])).fetchone()
                marker = " [active]" if active and active["active_group_id"] == g["id"] else ""
                print(f"    {m['name']:<18} {m['email']:<36}{marker}")

        print(f"\nPassword for all accounts: {DEMO_PASSWORD}")
        print("\n=== Login Guide ===")
        print("  Sharma Family members:")
        for email in ["rajesh@demo.receiptiq.in", "sunita@demo.receiptiq.in", "arjun@demo.receiptiq.in", "priya.sharma@demo.receiptiq.in"]:
            print(f"    {email}")
        print("  Others:")
        for email in ["student@demo.receiptiq.in", "professional@demo.receiptiq.in", "vikram@demo.receiptiq.in", "neha@demo.receiptiq.in", "riya@demo.receiptiq.in", "karan@demo.receiptiq.in"]:
            print(f"    {email}")

if __name__ == "__main__":
    run()
