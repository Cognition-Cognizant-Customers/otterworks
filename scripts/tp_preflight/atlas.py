#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import uuid
from requests import delete, get, post
from requests.auth import HTTPDigestAuth

from common import Manifest, require_env

require_env("MONGODB_ATLAS_PUBLIC_KEY", "MONGODB_ATLAS_PRIVATE_KEY", "MONGODB_ATLAS_PROJECT_ID")
base = "https://cloud.mongodb.com/api/atlas/v2"
project = os.environ["MONGODB_ATLAS_PROJECT_ID"]
auth = HTTPDigestAuth(os.environ["MONGODB_ATLAS_PUBLIC_KEY"], os.environ["MONGODB_ATLAS_PRIVATE_KEY"])
headers = {"Accept": "application/vnd.atlas.2024-08-05+json", "Content-Type": "application/json"}
m = Manifest("atlas")


def check(pid, description, method, url, **kwargs):
    try:
        r = method(url, auth=auth, headers=headers, timeout=30, **kwargs)
        result = "verified" if r.ok else "denied"
        m.add(pid, description, url, result, f"HTTP {r.status_code}: {r.text[:500]}")
        return r
    except Exception as exc:
        m.add(pid, description, url, "denied", str(exc))
        return None


groups = check("project-read", "Read the Atlas project", get, f"{base}/groups/{project}")
clusters = check("cluster-read", "Read cluster configuration", get, f"{base}/groups/{project}/clusters")
users = check("db-user-read", "Read database users", get, f"{base}/groups/{project}/databaseUsers")
if os.environ.get("MONGODB_ATLAS_URI"):
    try:
        from pymongo import MongoClient
        db = MongoClient(os.environ["MONGODB_ATLAS_URI"], serverSelectionTimeoutMS=10000)["tp_preflight"]
        name = f"ow_tp_preflight_{uuid.uuid4().hex}"
        db[name].insert_one({"_id": "probe"})
        db[name].delete_one({"_id": "probe"})
        m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "verified", "temporary collection cleaned")
        db.drop_collection(name)
    except Exception as exc:
        m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "denied", str(exc))
else:
    m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")

ip = None
try:
    ip = get("https://api.ipify.org", timeout=10).text.strip()
except Exception:
    ip = socket.gethostbyname(socket.gethostname())
entries = check("access-list-read", "Read the Atlas API access list", get, f"{base}/groups/{project}/accessList")
if entries is not None and entries.ok:
    listed = [x.get("ipAddress") or x.get("cidrBlock") for x in entries.json().get("links", [])]
    actual = entries.json().get("results", [])
    listed = [x for x in listed + [e.get("ipAddress") or e.get("cidrBlock") for e in actual] if x]
    m.add("vm-ip-listed", "The VM public IP is present in the Atlas access list", "Atlas accessList GET", "verified" if ip in listed else "denied", f"VM IP {ip}; entries={listed}")
probe_ip = "203.0.113.254"
created = check("access-list-post", "Create a temporary API access-list entry", post, f"{base}/groups/{project}/accessList", json={"ipAddress": probe_ip, "comment": "otterworks preflight"})
if created is not None and created.ok:
    entry_id = created.json().get("groupId") or created.json().get("id")
    check("access-list-delete", "Delete the temporary API access-list entry", delete, f"{base}/groups/{project}/accessList/{entry_id or probe_ip}")
elif created is not None:
    m.add("access-list-delete", "Delete the temporary API access-list entry", "Atlas accessList DELETE", "skipped", "POST did not create an entry")
raise SystemExit(m.write("atlas"))
