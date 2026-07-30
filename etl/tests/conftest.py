import configparser
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

FAKE_CONFIG = """
[aws]
access_key = test-access-key
secret_key = test-secret-key
region = us-east-1

[database]
host = localhost
port = 5432
database = otterworks
user = etl
password = etl-password

[services]
document_service_url = http://document-service:8000
file_service_url = http://file-service:8083
meilisearch_url = http://meilisearch:7700
meilisearch_api_key = test-meili-key

[s3]
data_lake_bucket = test-data-lake
file_storage_bucket = test-file-storage
quarantine_bucket = test-quarantine
archive_bucket = test-archive
analytics_prefix = analytics/daily
"""


def load_script(name):
    """Import an ETL script from etl/scripts as a module."""
    path = SCRIPTS_DIR / ("%s.py" % name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PreloadedConfigParser(configparser.ConfigParser):
    """ConfigParser that loads the test config regardless of the path read."""

    def read(self, filenames, encoding=None):
        self.read_string(FAKE_CONFIG)
        return [filenames] if isinstance(filenames, str) else list(filenames)


@pytest.fixture
def fake_config(monkeypatch):
    def patch(module):
        monkeypatch.setattr(module.configparser, "ConfigParser", PreloadedConfigParser)

    return patch


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeS3Client:
    def __init__(self, objects=None, list_pages=None):
        self.put_calls = []
        self.copy_calls = []
        self.delete_calls = []
        self.objects = objects or {}
        self.list_pages = list_pages or []

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "Body": Body, **kwargs})
        return {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError("NoSuchKey: %s/%s" % (Bucket, Key))
        import io

        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def copy_object(self, **kwargs):
        self.copy_calls.append(kwargs)
        return {}

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        return {}

    def get_paginator(self, name):
        pages = self.list_pages

        class Paginator:
            def paginate(self, **kwargs):
                return iter(pages)

        return Paginator()


class FakeBatchWriter:
    def __init__(self, sink):
        self.sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def delete_item(self, Key):
        self.sink.append(Key)


class FakeDynamoTable:
    def __init__(self, scan_pages):
        self.scan_pages = list(scan_pages)
        self._page = 0
        self.deleted_keys = []

    def scan(self, **kwargs):
        page = self.scan_pages[self._page]
        self._page += 1
        return page

    def batch_writer(self):
        return FakeBatchWriter(self.deleted_keys)


class FakeDynamoResource:
    def __init__(self, table):
        self._table = table

    def Table(self, name):
        return self._table
