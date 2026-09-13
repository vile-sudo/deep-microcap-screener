"""
Smoke test for accounts, watchlists and the account menu against a running server.

    BASE=http://127.0.0.1:8000 python backend/tests/smoke_accounts.py

The server must have AUTH_USERNAME=Owner / AUTH_PASSWORD=Owner-pass-123 and a
fresh database. Used by .github/workflows/postgres-check.yml.
"""
import os, sys
import requests
B=os.environ.get("BASE","http://127.0.0.1:8000"); fails=0
def check(n,c):
    global fails; print(("PASS " if c else "FAIL ")+n); fails+= not c
anon=requests.Session()
check("watchlist needs login", anon.get(B+"/api/watchlist").status_code==401 and anon.post(B+"/api/watchlist/SIKA").status_code==401)
adm=requests.Session(); adm.post(B+"/api/auth/login",json={"email":"Owner","password":"Owner-pass-123"})
users={}
for who in ("asha","ravi"):
    anon.post(B+"/api/auth/signup",json={"name":who,"email":f"{who}@example.com","password":"research-2026"})
    uid=next(u["id"] for u in adm.get(B+"/api/admin/users").json()["users"] if u["email"]==f"{who}@example.com")
    adm.post(B+f"/api/admin/users/{uid}/approve")
    s=requests.Session(); s.post(B+"/api/auth/login",json={"email":f"{who}@example.com","password":"research-2026"}); users[who]=(s,uid)
a,_=users["asha"]; r,rid=users["ravi"]
check("new user starts empty", a.get(B+"/api/watchlist").json()=={"accounts":True,"codes":[]})
a.post(B+"/api/watchlist/SIKA"); a.post(B+"/api/watchlist/QLINE"); a.post(B+"/api/watchlist/SIKA")
r.post(B+"/api/watchlist/KSB")
check("asha sees only her stars (no duplicates)", a.get(B+"/api/watchlist").json()["codes"]==["SIKA","QLINE"])
check("ravi sees only his", r.get(B+"/api/watchlist").json()["codes"]==["KSB"])
a2=requests.Session(); a2.post(B+"/api/auth/login",json={"email":"asha@example.com","password":"research-2026"})
check("same list from a second device", a2.get(B+"/api/watchlist").json()["codes"]==["SIKA","QLINE"])
a.delete(B+"/api/watchlist/SIKA")
check("unstar", a2.get(B+"/api/watchlist").json()["codes"]==["QLINE"])
check("merge keeps existing", a.put(B+"/api/watchlist",json={"codes":["KSB","QLINE"],"merge":True}).json()["codes"]==["QLINE","KSB"])
check("replace", a.put(B+"/api/watchlist",json={"codes":["SIKA"]}).json()["codes"]==["SIKA"])
check("bad code rejected", a.post(B+"/api/watchlist/..%2Fx").status_code in (400,404) and a.put(B+"/api/watchlist",json={"codes":["<script>"]}).status_code==400)
check("ravi unaffected", r.get(B+"/api/watchlist").json()["codes"]==["KSB"])
anon=requests.Session()
for u in ["/api/me/settings","/api/me/filters","/api/me/updates"]:
    check(f"{u} needs login", anon.get(B+u).status_code==401)
check("feedback needs login", anon.post(B+"/api/feedback",json={"message":"hello there"}).status_code==401)
def mk(who):
    anon.post(B+"/api/auth/signup",json={"name":who,"email":f"{who}@example.com","password":"research-2026"})
    uid=next(u["id"] for u in adm.get(B+"/api/admin/users").json()["users"] if u["email"]==f"{who}@example.com")
    adm.post(B+f"/api/admin/users/{uid}/approve")
    s=requests.Session(); s.post(B+"/api/auth/login",json={"email":f"{who}@example.com","password":"research-2026"}); return s,uid
a,aid=mk("meena"); r,rid=mk("tarun")
# settings
check("default prefs empty", a.get(B+"/api/me/settings").json()=={"prefs":{}})
check("dark saved", a.patch(B+"/api/me/settings",json={"prefs":{"dark":True,"evil":"x"}}).json()=={"prefs":{"dark":True}})
check("dark per user", r.get(B+"/api/me/settings").json()=={"prefs":{}} and a.get(B+"/api/me/settings").json()["prefs"]["dark"] is True)
# profile
j=a.patch(B+"/api/me/profile",json={"name":"  Asha   Rao "}).json()
check("rename", j["user"]["name"]=="Asha Rao" and a.get(B+"/api/auth/me").json()["user"]["name"]=="Asha Rao")
check("empty name refused", a.patch(B+"/api/me/profile",json={"name":"  "}).status_code==400)
# password
a2=requests.Session(); a2.post(B+"/api/auth/login",json={"email":"meena@example.com","password":"research-2026"})
check("wrong current password refused", a.post(B+"/api/me/password",json={"current":"nope-nope","new":"another-pass-1"}).status_code==400)
check("short new password refused", a.post(B+"/api/me/password",json={"current":"research-2026","new":"short"}).status_code==400)
check("password changed", a.post(B+"/api/me/password",json={"current":"research-2026","new":"another-pass-1"}).status_code==200)
check("this device stays logged in", a.get(B+"/api/me/settings").status_code==200)
check("other device logged out", a2.get(B+"/api/me/settings").status_code==401)
check("old password no longer works", requests.post(B+"/api/auth/login",json={"email":"meena@example.com","password":"research-2026"}).status_code==401)
check("new password works", requests.post(B+"/api/auth/login",json={"email":"meena@example.com","password":"another-pass-1"}).status_code==200)
check("main admin password change blocked", adm.post(B+"/api/me/password",json={"current":"Owner-pass-123","new":"whatever-123"}).status_code==400)
# saved filters
f=a.post(B+"/api/me/filters",json={"name":"Defence smallcaps","state":"#v=filters&tg=asme&w=SIKA,QLINE&sl=0%3A40"}).json()
check("filter saved, watchlist stripped", f["name"]=="Defence smallcaps" and "w=" not in f["state"] and "tg=asme" in f["state"])
check("junk state refused", a.post(B+"/api/me/filters",json={"name":"x","state":"<script>"}).status_code==400)
check("empty state refused", a.post(B+"/api/me/filters",json={"name":"x","state":""}).status_code==400)
check("ravi can't see asha's filters", r.get(B+"/api/me/filters").json()["filters"]==[])
check("ravi can't edit or delete it", r.patch(B+f"/api/me/filters/{f['id']}",json={"name":"mine"}).status_code==404 and r.delete(B+f"/api/me/filters/{f['id']}").status_code==404)
check("rename filter", a.patch(B+f"/api/me/filters/{f['id']}",json={"name":"Defence under 1000cr"}).json()["name"]=="Defence under 1000cr")
check("list", [x["name"] for x in a.get(B+"/api/me/filters").json()["filters"]]==["Defence under 1000cr"])
check("delete filter", a.delete(B+f"/api/me/filters/{f['id']}").status_code==200 and a.get(B+"/api/me/filters").json()["filters"]==[])
# updates
a.post(B+"/api/watchlist/QLINE")
u=a.get(B+"/api/me/updates").json()
kinds={i["kind"] for i in u["items"]}
check("updates have features and unread count", "feature" in kinds and u["unread"]==len(u["items"]) and u["unread"]>0)
rep=[i for i in u["items"] if i["kind"]=="report"]
check("report update flagged for watchlist", any(i.get("code")=="QLINE" and i["watch"]==["QLINE"] for i in rep) if rep else True)
a.post(B+"/api/me/updates/seen")
check("seen clears unread", a.get(B+"/api/me/updates").json()["unread"]==0 and r.get(B+"/api/me/updates").json()["unread"]>0)
# feedback
check("short message refused", a.post(B+"/api/feedback",json={"message":"hi"}).status_code==400)
check("message sent", a.post(B+"/api/feedback",json={"category":"data-error","message":"QLINE ROCE looks wrong on the table","page":"/#v=companies&q=qline"}).status_code==200)
check("non-admin can't read messages", a.get(B+"/api/admin/feedback").status_code==403)
m=adm.get(B+"/api/admin/feedback").json()
check("admin sees it as new", m["new"]==1 and m["messages"][0]["email"]=="meena@example.com" and m["messages"][0]["category"]=="data-error")
mid=m["messages"][0]["id"]
check("mark done", adm.post(B+f"/api/admin/feedback/{mid}/done").status_code==200 and adm.get(B+"/api/admin/feedback").json()["new"]==0)
check("delete message", adm.delete(B+f"/api/admin/feedback/{mid}").status_code==200)

# deleting a user removes their data: re-register the same email and find it empty
r.post(B+"/api/watchlist/SIKA"); r.post(B+"/api/me/filters",json={"name":"r","state":"v=gallery"}); r.patch(B+"/api/me/settings",json={"prefs":{"dark":True}})
check("admin deletes user", adm.delete(B+f"/api/admin/users/{rid}").status_code==200)
r2,_=mk("tarun")
check("re-registered user starts clean", r2.get(B+"/api/watchlist").json()["codes"]==[] and r2.get(B+"/api/me/filters").json()["filters"]==[] and r2.get(B+"/api/me/settings").json()=={"prefs":{}})
check("companies loaded", len(adm.get(B+"/api/companies").json())>300)
h=requests.get(B+"/healthz").json(); print("healthz", h)
print("fails", fails)
sys.exit(1 if fails else 0)
