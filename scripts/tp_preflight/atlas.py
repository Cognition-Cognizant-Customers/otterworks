#!/usr/bin/env python3
from __future__ import annotations

import os
import socket
import urllib.parse
import uuid
from requests import delete, get, post
from requests.auth import HTTPDigestAuth

from common import Manifest, require_env

require_env("MONGODB_ATLAS_PUBLIC_KEY", "MONGODB_ATLAS_PRIVATE_KEY", "MONGODB_ATLAS_PROJECT_ID")
base = os.environ.get("TP_ATLAS_API_BASE", "https://cloud.mongodb.com/api/atlas/v2").rstrip("/")
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
def api_entry_ip(entry):
    return entry.get("ipAddress") or entry.get("cidrBlock")


def api_entry_id(entry):
    return entry.get("ipAddress") or entry.get("cidrBlock") or entry.get("groupId") or entry.get("id")


def delete_entry(entry):
    entry_id = api_entry_id(entry)
    if not entry_id:
        return
    check("access-list-delete", "Delete a temporary API access-list entry", delete,
          f"{base}/groups/{project}/accessList/{urllib.parse.quote(entry_id, safe='')}")


def db_user_write():
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

ip = None
try:
    ip = get("https://api.ipify.org", timeout=10).text.strip()
except Exception:
    ip = socket.gethostbyname(socket.gethostname())
entries = check("access-list-read", "Read the Atlas API access list", get, f"{base}/groups/{project}/accessList")
listed = []
if entries is not None and entries.ok:
    listed = [api_entry_ip(entry) for entry in entries.json().get("results", []) if api_entry_ip(entry)]

probe_ip = os.environ.get("TP_ATLAS_TEST_IP", "203.0.113.254")
created = check("access-list-post", "Create a temporary API access-list entry", post,
                f"{base}/groups/{project}/accessList",
                json=[{"ipAddress": probe_ip, "comment": "otterworks preflight"}])
created_entry = None
try:
    if created is not None and created.ok:
        body = created.json()
        created_entry = (body.get("results") or [body])[0] if isinstance(body, dict) else body[0]
    if ip in listed:
        m.add("vm-ip-listed", "The VM public IP is present in the Atlas access list",
              "Atlas accessList GET", "verified", f"VM IP {ip}; entries={listed}")
        if os.environ.get("MONGODB_ATLAS_URI"):
            db_user_write()
        else:
            m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                  "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
    else:
        own = check("access-list-post-own-ip", "Temporarily add the VM IP for the DB write probe",
                    post, f"{base}/groups/{project}/accessList",
                    json=[{"ipAddress": ip, "comment": "otterworks preflight temporary access"}])
        own_entry = None
        try:
            if own is not None and own.ok:
                body = own.json()
                own_entry = (body.get("results") or [body])[0] if isinstance(body, dict) else body[0]
                m.add("vm-ip-listed", "The VM public IP can be self-healed for the DB write path",
                      "Atlas accessList POST/DELETE", "verified", f"VM IP {ip} was absent and temporary add succeeded")
                if os.environ.get("MONGODB_ATLAS_URI"):
                    db_user_write()
                else:
                    m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                          "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
            else:
                m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
                      "Atlas accessList POST/DELETE", "denied", f"VM IP {ip}; entries={listed}")
        finally:
            if own_entry:
                delete_entry(own_entry)
finally:
    if created_entry:
        # If later probing failed, still remove the TEST-NET entry.
        delete_entry(created_entry)
raise SystemExit(m.write("atlas"))
