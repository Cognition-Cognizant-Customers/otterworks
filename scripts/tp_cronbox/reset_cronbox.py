#!/usr/bin/env python3
"""Restore the golden application's id-keyed audit table."""

from __future__ import annotations

from common import clients


def main():
    _, dynamo, _ = clients()
    client = dynamo.meta.client
    try:
        client.delete_table(TableName="otterworks-audit-events")
        client.get_waiter("table_not_exists").wait(TableName="otterworks-audit-events")
    except client.exceptions.ResourceNotFoundException:
        pass
    client.create_table(
        TableName="otterworks-audit-events",
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName="otterworks-audit-events")


if __name__ == "__main__":
    main()
