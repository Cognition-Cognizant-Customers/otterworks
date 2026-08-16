#!/usr/bin/env python3
from __future__ import annotations

import os
import ipaddress
import signal
import socket
import urllib.parse
import uuid
from requests import delete, get, post
from requests.auth import HTTPDigestAuth

from common import Manifest, exception_detail, require_env

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
        detail = f"HTTP {r.status_code}"
        if not r.ok:
            try:
                body = r.json()
                detail += f": {body.get('errorCode') or body.get('detail') or body.get('error') or body.get('message') or 'request failed'}"
            except ValueError:
                detail += ": request failed"
        m.add(pid, description, url, result, detail)
        return r
    except Exception as exc:
        m.add(pid, description, url, "denied", exception_detail(exc))
        return None


groups = check("project-read", "Read the Atlas project", get, f"{base}/groups/{project}")
clusters = check("cluster-read", "Read cluster configuration", get, f"{base}/groups/{project}/clusters")
users = check("db-user-read", "Read database users", get, f"{base}/groups/{project}/databaseUsers")
def api_entry_ip(entry):
    return entry.get("ipAddress") or entry.get("cidrBlock")


def covers(ip, entries):
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in entries:
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def delete_entry(entry):
    entry_id = entry.get("ipAddress") or entry.get("cidrBlock")
    label = entry_id or repr(entry)
    if not entry_id:
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "denied",
              f"entry {label} has no IP or CIDR; manual access-list cleanup required")
        return False
    url = f"{base}/groups/{project}/accessList/{urllib.parse.quote(entry_id, safe='')}"
    try:
        response = delete(url, auth=auth, headers=headers, timeout=30)
        if response.ok:
            m.add("access-list-delete", "Delete a temporary API access-list entry",
                  "Atlas accessList DELETE", "verified", f"HTTP {response.status_code}: {label}")
            return True
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "denied",
              f"HTTP {response.status_code}: {label}; manual access-list cleanup required")
    except Exception as exc:
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "denied",
              f"{label}; manual access-list cleanup required: {exc}")
    return False


def db_user_write():
    try:
        from pymongo import MongoClient
        db = MongoClient(os.environ["MONGODB_ATLAS_URI"], serverSelectionTimeoutMS=10000)["tp_preflight"]
        name = f"ow_tp_preflight_{uuid.uuid4().hex}"
        db[name].insert_one({"_id": "probe"})
        db[name].delete_one({"_id": "probe"})
        db.drop_collection(name)
        m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "verified", "temporary collection cleaned")
    except Exception as exc:
        m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "denied", exception_detail(exc))


def access_list_snapshot():
    entries = []
    page = 1
    total = None
    items_per_page = 100
    while True:
        url = f"{base}/groups/{project}/accessList?pageNum={page}&itemsPerPage={items_per_page}"
        try:
            response = get(url, auth=auth, headers=headers, timeout=30)
            if not response.ok:
                m.add("access-list-read", "Read the Atlas API access list", url, "denied", f"HTTP {response.status_code}")
                return None
            body = response.json()
            page_entries = body.get("results")
            if not isinstance(page_entries, list):
                m.add("access-list-read", "Read the Atlas API access list", url, "denied", "response missing results list")
                return None
            entries.extend(page_entries)
            total = body.get("totalCount")
            if (total is not None and len(entries) >= total) or len(page_entries) < items_per_page:
                break
            page += 1
        except Exception as exc:
            m.add("access-list-read", "Read the Atlas API access list", url, "denied", exception_detail(exc))
            return None
    m.add("access-list-read", "Read the Atlas API access list",
          f"{base}/groups/{project}/accessList", "verified",
          f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} across {page} page(s)")
    return entries


def validate_probe_ip(value):
    try:
        if "/" in value:
            interface = ipaddress.ip_interface(value)
            if interface.version != 4 or interface.network.prefixlen != 32:
                raise ValueError
            address = interface.ip
        else:
            address = ipaddress.ip_address(value)
            if address.version != 4:
                raise ValueError
    except ValueError:
        raise SystemExit(
            "TP_ATLAS_TEST_IP must be an IPv4 host (optionally /32) in "
            "192.0.2.0/24, 198.51.100.0/24, or 203.0.113.0/24"
        )
    allowed = (
        ipaddress.ip_network("192.0.2.0/24"),
        ipaddress.ip_network("198.51.100.0/24"),
        ipaddress.ip_network("203.0.113.0/24"),
    )
    if not any(address in network for network in allowed):
        raise SystemExit(
            "TP_ATLAS_TEST_IP must be in 192.0.2.0/24, 198.51.100.0/24, "
            "or 203.0.113.0/24"
        )
    return str(address)


ip = None
try:
    ip = get("https://api.ipify.org", timeout=10).text.strip()
except Exception:
    ip = socket.gethostbyname(socket.gethostname())
entry_records = access_list_snapshot()
probe_ip = validate_probe_ip(os.environ.get("TP_ATLAS_TEST_IP", "203.0.113.254"))
if entry_records is None:
    m.add("access-list-post", "Create a temporary API access-list entry",
          "Atlas accessList POST", "skipped", "access-list snapshot failed; no mutation attempted")
    m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
          "Atlas accessList GET", "skipped", "access-list snapshot failed; no mutation attempted")
    m.add("db-user-write", "Insert and delete a temporary document with the DB user",
          "MongoDB wire protocol", "skipped", "access-list snapshot failed; no mutation attempted")
    raise SystemExit(m.write("atlas"))
listed = [api_entry_ip(entry) for entry in entry_records if api_entry_ip(entry)]
created_entry = None
own_entry = None


def cleanup_entries():
    global created_entry, own_entry
    if own_entry:
        delete_entry(own_entry)
        own_entry = None
    if created_entry:
        delete_entry(created_entry)
        created_entry = None


def handle_signal(signum, _frame):
    cleanup_entries()
    raise SystemExit(128 + signum)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
try:
    if covers(probe_ip, listed):
        m.add("access-list-post", "Create a temporary API access-list entry",
              "Atlas accessList POST", "skipped", f"{probe_ip} is already covered")
    else:
        created = check("access-list-post", "Create a temporary API access-list entry", post,
                        f"{base}/groups/{project}/accessList",
                        json=[{"ipAddress": probe_ip, "comment": "otterworks preflight"}])
        if created is not None and created.ok:
            created_entry = {"ipAddress": probe_ip}
    if covers(ip, listed):
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
        try:
            if own is not None and own.ok:
                own_entry = {"ipAddress": ip}
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
                own_entry = None
finally:
    cleanup_entries()
raise SystemExit(m.write("atlas"))
