#!/usr/bin/env python3
from __future__ import annotations

import os
import ipaddress
import re
import signal
import sys
import urllib.parse
import uuid
from requests import delete, get, post
from requests.auth import HTTPDigestAuth

from common import Manifest, exception_detail, require_env

require_env("MONGODB_ATLAS_PUBLIC_KEY", "MONGODB_ATLAS_PRIVATE_KEY", "MONGODB_ATLAS_PROJECT_ID")
raw_base = os.environ.get("TP_ATLAS_API_BASE", "https://cloud.mongodb.com/api/atlas/v2")
parsed_base = urllib.parse.urlparse(raw_base)
if parsed_base.scheme != "https" or not parsed_base.hostname or parsed_base.username or parsed_base.password:
    raise SystemExit("TP_ATLAS_API_BASE must be an HTTPS URL with a valid host")
if parsed_base.hostname != "cloud.mongodb.com" and os.environ.get("TP_ATLAS_ALLOW_CUSTOM_API_BASE") != "1":
    raise SystemExit("TP_ATLAS_API_BASE must use cloud.mongodb.com unless TP_ATLAS_ALLOW_CUSTOM_API_BASE=1")
base = raw_base.rstrip("/")
project = os.environ["MONGODB_ATLAS_PROJECT_ID"]
if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
    raise SystemExit("MONGODB_ATLAS_PROJECT_ID must contain only letters, digits, '_' or '-'")
auth = HTTPDigestAuth(os.environ["MONGODB_ATLAS_PUBLIC_KEY"], os.environ["MONGODB_ATLAS_PRIVATE_KEY"])
headers = {"Accept": "application/vnd.atlas.2024-08-05+json", "Content-Type": "application/json"}
m = Manifest("atlas")
run_marker = f"otterworks preflight {uuid.uuid4().hex}"


def handle_uncaught(exc_type, exc, traceback):
    try:
        m.add("internal-error", "Unhandled preflight failure", "preflight runtime",
              "denied", exception_detail(exc))
        m.write("atlas")
    finally:
        sys.__excepthook__(exc_type, exc, traceback)


sys.excepthook = handle_uncaught


def check(pid, description, method, url, **kwargs):
    try:
        r = method(url, auth=auth, headers=headers, timeout=30, **kwargs)
        result = "verified" if r.ok else "denied"
        detail = f"HTTP {r.status_code}"
        if not r.ok:
            try:
                body = r.json()
                if isinstance(body, dict):
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
    current = access_list_snapshot(record=False)
    if current is None:
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "denied",
              f"{label}; could not verify ownership; manual access-list cleanup required")
        return False
    current_entry = next((item for item in current if entry_matches(item, entry)), None)
    if current_entry is None:
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "verified", f"{label} was already absent")
        return True
    comment = current_entry.get("comment")
    if not isinstance(comment, str):
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "denied",
              f"{label}; ownership comment unavailable; manual access-list cleanup required")
        return False
    if run_marker not in comment:
        m.add("access-list-delete", "Delete a temporary API access-list entry",
              "Atlas accessList DELETE", "informational",
              f"{label} was not created by this run and was left in place")
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
        db = MongoClient(os.environ["MONGODB_ATLAS_URI"], serverSelectionTimeoutMS=10000)["ow_tp_preflight"]
        name = f"ow_tp_preflight_{uuid.uuid4().hex}"
        db[name].insert_one({"_id": "probe"})
        db[name].delete_one({"_id": "probe"})
        db.drop_collection(name)
        m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "verified", "temporary collection cleaned")
    except Exception as exc:
        m.add("db-user-write", "Insert and delete a temporary document with the DB user", "MongoDB wire protocol", "denied", exception_detail(exc))


def access_list_snapshot(record=True):
    entries = []
    page = 1
    total = None
    items_per_page = 100
    while True:
        url = f"{base}/groups/{project}/accessList?pageNum={page}&itemsPerPage={items_per_page}"
        try:
            response = get(url, auth=auth, headers=headers, timeout=30)
            if not response.ok:
                if record:
                    m.add("access-list-read", "Read the Atlas API access list", url, "denied", f"HTTP {response.status_code}")
                return None
            body = response.json()
            page_entries = body.get("results")
            if not isinstance(page_entries, list):
                if record:
                    m.add("access-list-read", "Read the Atlas API access list", url, "denied", "response missing results list")
                return None
            entries.extend(page_entries)
            total = body.get("totalCount")
            if (total is not None and len(entries) >= total) or len(page_entries) < items_per_page:
                break
            page += 1
        except Exception as exc:
            if record:
                m.add("access-list-read", "Read the Atlas API access list", url, "denied", exception_detail(exc))
            return None
    if record:
        m.add("access-list-read", "Read the Atlas API access list",
              f"{base}/groups/{project}/accessList", "verified",
              f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'} across {page} page(s)")
    return entries


def entry_matches(entry, target):
    return api_entry_ip(entry) == api_entry_ip(target)


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
    response = get("https://api.ipify.org", timeout=10)
    response.raise_for_status()
    address = ipaddress.ip_address(response.text.strip())
    if address.version == 4:
        ip = str(address)
except Exception:
    ip = None
probe_ip = validate_probe_ip(os.environ.get("TP_ATLAS_TEST_IP", "203.0.113.254"))
if ip is None:
    m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
          "Atlas accessList GET", "skipped", "could not determine the VM public address")
    m.add("db-user-write", "Insert and delete a temporary document with the DB user",
          "MongoDB wire protocol", "skipped", "could not determine the VM public address")
    raise SystemExit(m.write("atlas"))
entry_records = access_list_snapshot()
if entry_records is None:
    m.add("access-list-post", "Create a temporary API access-list entry",
          "Atlas accessList POST", "skipped", "access-list snapshot failed; no mutation attempted")
    m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
          "Atlas accessList GET", "skipped", "access-list snapshot failed; no mutation attempted")
    m.add("db-user-write", "Insert and delete a temporary document with the DB user",
          "MongoDB wire protocol", "skipped", "access-list snapshot failed; no mutation attempted")
    raise SystemExit(m.write("atlas"))
listed = [api_entry_ip(entry) for entry in entry_records if api_entry_ip(entry)]
cleanup_registry = {}


def register_cleanup(entry):
    cleanup_registry[api_entry_ip(entry)] = entry


def reconcile_ambiguous(entry):
    current = access_list_snapshot(record=False)
    if current is None:
        m.add("access-list-ambiguous-cleanup", "Reconcile an ambiguous access-list create",
              "Atlas accessList GET", "denied",
              f"{api_entry_ip(entry)} may have been created; manual access-list cleanup required")
        return
    if any(entry_matches(item, entry) for item in current):
        delete_entry(entry)
    else:
        m.add("access-list-ambiguous-cleanup", "Reconcile an ambiguous access-list create",
              "Atlas accessList GET", "verified", f"{api_entry_ip(entry)} was not present")
    cleanup_registry.pop(api_entry_ip(entry), None)


def cleanup_entries():
    for key, entry in list(cleanup_registry.items()):
        delete_entry(entry)
        cleanup_registry.pop(key, None)


def handle_signal(signum, _frame):
    cleanup_entries()
    m.write("atlas")
    raise SystemExit(128 + signum)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
try:
    if covers(probe_ip, listed):
        m.add("access-list-post", "Create a temporary API access-list entry",
              "Atlas accessList POST", "skipped", f"{probe_ip} is already covered")
    else:
        created_entry = {"ipAddress": probe_ip, "comment": run_marker}
        register_cleanup(created_entry)
        created = check("access-list-post", "Create a temporary API access-list entry", post,
                        f"{base}/groups/{project}/accessList",
                        json=[{"ipAddress": probe_ip, "comment": run_marker}])
        if created is None or not created.ok:
            reconcile_ambiguous(created_entry)
    if covers(ip, listed):
        m.add("vm-ip-listed", "The VM public IP is present in the Atlas access list",
              "Atlas accessList GET", "verified", f"VM IP {ip}; covered by {len(listed)} access-list entr{'y' if len(listed) == 1 else 'ies'}")
        if os.environ.get("MONGODB_ATLAS_URI"):
            db_user_write()
        else:
            m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                  "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
    else:
        own_entry = {"ipAddress": ip, "comment": run_marker}
        register_cleanup(own_entry)
        own = check("access-list-post-own-ip", "Temporarily add the VM IP for the DB write probe",
                    post, f"{base}/groups/{project}/accessList",
                    json=[{"ipAddress": ip, "comment": run_marker}])
        try:
            if own is not None and own.ok:
                m.add("vm-ip-listed", "The VM public IP can be self-healed for the DB write path",
                      "Atlas accessList POST/DELETE", "verified", f"VM IP {ip} was absent and temporary add succeeded")
                if os.environ.get("MONGODB_ATLAS_URI"):
                    db_user_write()
                else:
                    m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                          "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
            else:
                reconcile_ambiguous(own_entry)
                m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
                      "Atlas accessList POST/DELETE", "denied", f"VM IP {ip}; access-list entries checked={len(listed)}")
                m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                      "MongoDB wire protocol", "skipped",
                      "VM IP could not be temporarily allow-listed")
        finally:
            cleanup_entries()
finally:
    cleanup_entries()
raise SystemExit(m.write("atlas"))
