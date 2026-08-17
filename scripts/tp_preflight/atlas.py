#!/usr/bin/env python3
from __future__ import annotations

import os
import ipaddress
import re
import time
import urllib.parse
import uuid
from requests import delete, get, post
from requests.auth import HTTPDigestAuth

from common import (
    Manifest,
    exception_detail,
    install_excepthook,
    install_signal_handlers,
    require_env,
    validate_https_endpoint,
)


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


require_env("MONGODB_ATLAS_PUBLIC_KEY", "MONGODB_ATLAS_PRIVATE_KEY", "MONGODB_ATLAS_PROJECT_ID")
probe_ip = validate_probe_ip(os.environ.get("TP_ATLAS_TEST_IP", "203.0.113.254"))
raw_base = os.environ.get("TP_ATLAS_API_BASE", "https://cloud.mongodb.com/api/atlas/v2")
parsed_base = validate_https_endpoint(raw_base, "TP_ATLAS_API_BASE")
if parsed_base.hostname != "cloud.mongodb.com":
    raise SystemExit("TP_ATLAS_API_BASE must use cloud.mongodb.com")
base = raw_base.rstrip("/")
project = os.environ["MONGODB_ATLAS_PROJECT_ID"]
if not re.fullmatch(r"[A-Za-z0-9_-]+", project):
    raise SystemExit("MONGODB_ATLAS_PROJECT_ID must contain only letters, digits, '_' or '-'")
auth = HTTPDigestAuth(os.environ["MONGODB_ATLAS_PUBLIC_KEY"], os.environ["MONGODB_ATLAS_PRIVATE_KEY"])
headers = {"Accept": "application/vnd.atlas.2024-08-05+json", "Content-Type": "application/json"}
m = Manifest("atlas")
install_excepthook(m, "atlas")
run_marker = f"otterworks-preflight-{uuid.uuid4().hex}"


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
              f"{label}; manual access-list cleanup required: {exception_detail(exc)}")
    return False


pending_collections = {}
pending_validator_collections = {}


def reconcile_collection(name, emit=True):
    entry = pending_collections.pop(name, None)
    if entry is None:
        return None
    client, database = entry
    try:
        database.drop_collection(name)
        outcome = ("verified", f"{name} confirmed absent")
    except Exception as exc:
        outcome = ("denied",
                   f"{name} may remain in ow_tp_preflight; manual cleanup required: {exception_detail(exc)}")
    finally:
        try:
            client.close()
        except Exception:
            pass
    if emit:
        m.add("db-user-write-cleanup", "Drop the temporary probe collection",
              "MongoDB wire protocol", outcome[0], outcome[1])
    return outcome


def reconcile_collections():
    for name in list(pending_collections):
        reconcile_collection(name)


def reconcile_validator_collections():
    for name, (client, database) in list(pending_validator_collections.items()):
        try:
            database.drop_collection(name)
            m.add("validator-ddl-cleanup", "Drop the validator DDL probe collection",
                  "MongoDB wire protocol", "verified", f"{name} confirmed absent")
        except Exception as exc:
            m.add("validator-ddl-cleanup", "Drop the validator DDL probe collection",
                  "MongoDB wire protocol", "denied",
                  f"{name} may remain in ow_tp_preflight; manual cleanup required: {exception_detail(exc)}")
        finally:
            pending_validator_collections.pop(name, None)
            try:
                client.close()
            except Exception:
                pass


def db_user_write():
    from pymongo import MongoClient

    last_error = None
    cleanup_outcomes = []
    for attempt in range(3):
        client = None
        name = f"ow_tp_preflight_{uuid.uuid4().hex}"
        created = False
        try:
            client = MongoClient(os.environ["MONGODB_ATLAS_URI"], serverSelectionTimeoutMS=10000)
            database = client["ow_tp_preflight"]
            database[name].insert_one({"_id": "probe"})
            created = True
            pending_collections[name] = (client, database)
            database[name].delete_one({"_id": "probe"})
            database.drop_collection(name)
            pending_collections.pop(name, None)
            client.close()
            if any(result == "denied" for result, _ in cleanup_outcomes):
                m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                      "MongoDB wire protocol", "denied",
                      "write succeeded on retry, but a prior temporary collection cleanup failed; manual cleanup required")
                for result, detail in cleanup_outcomes:
                    m.add("db-user-write-cleanup", "Drop the temporary probe collection",
                          "MongoDB wire protocol", result, detail)
                return
            m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                  "MongoDB wire protocol", "verified", "temporary collection cleaned")
            return
        except Exception as exc:
            last_error = exc
            if created:
                outcome = reconcile_collection(name, emit=False)
                if outcome is not None:
                    cleanup_outcomes.append(outcome)
            elif client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            if attempt < 2:
                time.sleep(5)
    m.add("db-user-write", "Insert and delete a temporary document with the DB user",
          "MongoDB wire protocol", "denied", exception_detail(last_error))
    for result, detail in cleanup_outcomes:
        m.add("db-user-write-cleanup", "Drop the temporary probe collection",
              "MongoDB wire protocol", result, detail)


def validator_ddl():
    if not os.environ.get("MONGODB_ATLAS_URI"):
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "denied", "MONGODB_ATLAS_URI is not set")
        return
    client = None
    name = f"ow_tp_preflight_validator_{uuid.uuid4().hex}"
    try:
        from pymongo import MongoClient
        from pymongo.errors import WriteError

        client = MongoClient(os.environ["MONGODB_ATLAS_URI"], serverSelectionTimeoutMS=10000)
        database = client["ow_tp_preflight"]
        pending_validator_collections[name] = (client, database)
        database.create_collection(
            name,
            validator={
                "$jsonSchema": {
                    "bsonType": "object",
                    "required": ["kind"],
                    "properties": {"kind": {"bsonType": "string"}},
                }
            },
            validationLevel="strict",
            validationAction="error",
        )
        try:
            database[name].insert_one({"_id": "invalid", "kind": 42})
        except WriteError:
            pass
        else:
            raise RuntimeError("the validator accepted a violating document")
        database[name].insert_one({"_id": "valid", "kind": "conforming"})
        database[name].delete_one({"_id": "valid"})
        database.drop_collection(name)
        pending_validator_collections.pop(name, None)
        client.close()
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "verified",
              "$jsonSchema validator rejected a violating insert and accepted a conforming insert; collection cleaned")
    except Exception as exc:
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "denied", exception_detail(exc))
        reconcile_validator_collections()
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def alert_configs_snapshot(record=False):
    url = f"{base}/groups/{project}/alertConfigs"
    try:
        response = get(url, auth=auth, headers=headers, timeout=30)
        if not response.ok:
            if record:
                check("alert-config-read", "Read Atlas project alert configurations", get, url)
            return None
        body = response.json()
        results = body.get("results")
        if not isinstance(results, list):
            return None
        return results
    except Exception:
        return None


def alert_marker(config):
    for notification in config.get("notifications") or []:
        if isinstance(notification, dict) and run_marker in str(notification.get("webhookUrl", "")):
            return True
    return False


def alert_config_id(config):
    return config.get("id") or config.get("alertConfigId")


alert_cleanup_registry = {}


def delete_alert_config(alert_id):
    url = f"{base}/groups/{project}/alertConfigs/{urllib.parse.quote(str(alert_id), safe='')}"
    try:
        response = delete(url, auth=auth, headers=headers, timeout=30)
        if response.ok:
            m.add("alert-webhook-config-cleanup", "Delete the temporary webhook alert configuration",
                  "Atlas alertConfigs DELETE", "verified", f"{alert_id} removed")
            return True
        detail = f"HTTP {response.status_code}"
        try:
            body = response.json()
            detail += f": {body.get('errorCode') or body.get('detail') or body.get('error') or body.get('message') or 'request failed'}"
        except ValueError:
            detail += ": request failed"
        m.add("alert-webhook-config-cleanup", "Delete the temporary webhook alert configuration",
              "Atlas alertConfigs DELETE", "denied",
              f"{alert_id}; manual cleanup required: {detail}")
    except Exception as exc:
        m.add("alert-webhook-config-cleanup", "Delete the temporary webhook alert configuration",
              "Atlas alertConfigs DELETE", "denied",
              f"{alert_id}; manual cleanup required: {exception_detail(exc)}")
    return False


def reconcile_alert_configs():
    for alert_id in list(alert_cleanup_registry):
        current = alert_configs_snapshot()
        if current is None:
            m.add("alert-webhook-config-cleanup", "Reconcile the temporary webhook alert configuration",
                  "Atlas alertConfigs GET", "denied",
                  f"{alert_id}; manual cleanup required")
        elif any(str(alert_config_id(item)) == str(alert_id) and alert_marker(item) for item in current):
            delete_alert_config(alert_id)
        else:
            m.add("alert-webhook-config-cleanup", "Reconcile the temporary webhook alert configuration",
                  "Atlas alertConfigs GET", "verified", f"{alert_id} was already absent")
        alert_cleanup_registry.pop(alert_id, None)


def alert_webhook_config():
    integrations_url = f"{base}/groups/{project}/integrations"
    check("alert-integrations-read", "Read Atlas project third-party integrations",
          get, integrations_url)
    alert_url = f"{base}/groups/{project}/alertConfigs"
    alert_read = check("alert-config-read", "Read Atlas project alert configurations", get, alert_url)
    if alert_read is None:
        return
    webhook_url = f"https://example.com/otterworks-tp-preflight/{run_marker}"
    payload = {
        "description": run_marker,
        "enabled": True,
        "eventTypeName": "HOST_DOWN",
        "notifications": [
            {"delayMin": 0, "typeName": "WEBHOOK", "webhookUrl": webhook_url}
        ],
    }
    created = check("alert-webhook-config", "Create a temporary webhook-notification alert configuration",
                    post, alert_url, json=payload)
    alert_id = None
    if created is not None and created.ok:
        try:
            body = created.json()
            alert_id = alert_config_id(body)
        except ValueError:
            pass
        if not alert_id:
            current = alert_configs_snapshot()
            matches = [item for item in (current or []) if alert_marker(item)]
            for item in matches:
                item_id = alert_config_id(item)
                if item_id:
                    alert_cleanup_registry[str(item_id)] = item
            if len(matches) == 1:
                alert_id = alert_config_id(matches[0])
    if alert_id:
        alert_cleanup_registry[str(alert_id)] = payload
        reconcile_alert_configs()
    elif created is None or not created.ok:
        current = alert_configs_snapshot()
        matches = [item for item in (current or []) if alert_marker(item)]
        for item in matches:
            item_id = alert_config_id(item)
            if item_id:
                alert_cleanup_registry[str(item_id)] = item
        if matches:
            reconcile_alert_configs()
        elif current is None:
            m.add("alert-webhook-config-cleanup", "Reconcile an ambiguous webhook alert configuration create",
                  "Atlas alertConfigs GET", "denied",
                  "create outcome was ambiguous; manual cleanup required")
    else:
        m.add("alert-webhook-config-cleanup", "Reconcile a temporary webhook-notification alert configuration",
              "Atlas alertConfigs POST", "denied",
              f"create succeeded but no alert id was returned or found; run marker {run_marker}; manual cleanup required")


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


ip = None
ip_lookup_error = None
if os.environ.get("MONGODB_ATLAS_URI"):
    try:
        response = get("https://api.ipify.org", timeout=10)
        response.raise_for_status()
        address = ipaddress.ip_address(response.text.strip())
        if address.version == 4:
            ip = str(address)
        else:
            ip_lookup_error = "public address was not IPv4"
    except Exception as exc:
        ip_lookup_error = exception_detail(exc)
entry_records = access_list_snapshot()
if entry_records is None:
    m.add("access-list-post", "Create a temporary API access-list entry",
          "Atlas accessList POST", "denied", "access-list snapshot failed; no mutation attempted")
    m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
          "Atlas accessList GET", "denied", "access-list snapshot failed; no mutation attempted")
    if os.environ.get("MONGODB_ATLAS_URI"):
        m.add("db-user-write", "Insert and delete a temporary document with the DB user",
              "MongoDB wire protocol", "denied", "access-list snapshot failed; no mutation attempted")
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "denied", "access-list snapshot failed; no mutation attempted")
    else:
        m.add("db-user-write", "Insert and delete a temporary document with the DB user",
              "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
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
    reconcile_collections()
    reconcile_validator_collections()
    reconcile_alert_configs()


install_signal_handlers(m, "atlas", cleanup_entries)
try:
    if probe_ip in listed or f"{probe_ip}/32" in listed:
        m.add("access-list-post", "Create a temporary API access-list entry",
              "Atlas accessList POST", "skipped", f"{probe_ip} is already listed exactly")
    else:
        created_entry = {"ipAddress": probe_ip, "comment": run_marker}
        register_cleanup(created_entry)
        created = check("access-list-post", "Create a temporary API access-list entry", post,
                        f"{base}/groups/{project}/accessList",
                        json=[{"ipAddress": probe_ip, "comment": run_marker}])
        if created is None or not created.ok:
            reconcile_ambiguous(created_entry)
    if not os.environ.get("MONGODB_ATLAS_URI"):
        m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
              "Atlas accessList GET", "skipped", "MONGODB_ATLAS_URI is not set")
        m.add("db-user-write", "Insert and delete a temporary document with the DB user",
              "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "skipped", "MONGODB_ATLAS_URI is not set")
    elif ip is None:
        m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
              "Atlas accessList GET", "denied",
              f"could not determine the VM public address: {ip_lookup_error or 'unknown lookup failure'}")
        m.add("db-user-write", "Insert and delete a temporary document with the DB user",
              "MongoDB wire protocol", "denied",
              f"could not determine the VM public address: {ip_lookup_error or 'unknown lookup failure'}")
        m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
              "MongoDB wire protocol", "denied",
              f"could not determine the VM public address: {ip_lookup_error or 'unknown lookup failure'}")
    elif covers(ip, listed):
        m.add("vm-ip-listed", "The VM public IP is present in the Atlas access list",
              "Atlas accessList GET", "verified", f"VM IP {ip}; covered by {len(listed)} access-list entr{'y' if len(listed) == 1 else 'ies'}")
        db_user_write()
        validator_ddl()
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
                time.sleep(10)
                db_user_write()
                validator_ddl()
            else:
                reconcile_ambiguous(own_entry)
                m.add("vm-ip-listed", "The VM public IP is present or can be self-healed in the Atlas access list",
                      "Atlas accessList POST/DELETE", "denied", f"VM IP {ip}; access-list entries checked={len(listed)}")
                m.add("db-user-write", "Insert and delete a temporary document with the DB user",
                      "MongoDB wire protocol",
                      "denied" if os.environ.get("MONGODB_ATLAS_URI") else "skipped",
                      "VM IP could not be temporarily allow-listed"
                      if os.environ.get("MONGODB_ATLAS_URI")
                      else "MONGODB_ATLAS_URI is not set")
                m.add("validator-ddl", "Create and exercise a MongoDB $jsonSchema validator",
                      "MongoDB wire protocol",
                      "denied" if os.environ.get("MONGODB_ATLAS_URI") else "skipped",
                      "VM IP could not be temporarily allow-listed"
                      if os.environ.get("MONGODB_ATLAS_URI")
                      else "MONGODB_ATLAS_URI is not set")
        finally:
            cleanup_entries()
    alert_webhook_config()
finally:
    cleanup_entries()
raise SystemExit(m.write("atlas"))
