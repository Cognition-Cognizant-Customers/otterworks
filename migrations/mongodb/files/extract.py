"""Extractor — paginated DynamoDB scan of one namespace's file-metadata slice.

The source table is shared across namespaces, so every page is filtered on the
`ns` attribute server-side. Pages are yielded as they arrive and batched for the
loader, so the whole slice is never held in memory (500k items at `SCALE=full`).
"""

from collections.abc import Iterator

from common import dynamo_table

# Attribute names are the file-service's, verbatim.
PROJECTION = (
    "id, #ns, #name, mime_type, size_bytes, s3_key, folder_id, owner_id, "
    "#version, is_trashed, created_at, updated_at"
)
# `ns`, `name` and `version` are DynamoDB reserved words.
ATTRIBUTE_NAMES = {"#ns": "ns", "#name": "name", "#version": "version"}


def scan_items(ns: str, table=None) -> Iterator[dict]:
    """Yield every item of the namespace's slice, one page at a time."""
    table = table if table is not None else dynamo_table()
    scan_kwargs = {
        "ProjectionExpression": PROJECTION,
        "FilterExpression": "#ns = :ns",
        "ExpressionAttributeNames": ATTRIBUTE_NAMES,
        "ExpressionAttributeValues": {":ns": ns},
    }
    while True:
        response = table.scan(**scan_kwargs)
        yield from response.get("Items", [])
        last_key = response.get("LastEvaluatedKey")
        if last_key is None:
            return
        scan_kwargs["ExclusiveStartKey"] = last_key


def batched(items: Iterator[dict], size: int) -> Iterator[list[dict]]:
    """Group an item stream into lists of at most `size` items."""
    if size < 1:
        raise ValueError("batch size must be >= 1")
    batch: list[dict] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
