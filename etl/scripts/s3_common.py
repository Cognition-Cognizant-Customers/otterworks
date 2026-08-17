# s3_common.py - Shared S3 helpers for the ETL scripts

import os


def bucket_owner_args(config=None):
    """Return {"ExpectedBucketOwner": <account id>} when an owner is configured.

    Sourced from AWS_EXPECTED_BUCKET_OWNER, falling back to the
    [aws] expected_bucket_owner entry in config.ini. Returns an empty dict when
    unset so LocalStack-based local runs keep working.
    """
    owner = os.environ.get("AWS_EXPECTED_BUCKET_OWNER", "").strip()

    if not owner and config is not None and config.has_option("aws", "expected_bucket_owner"):
        owner = config.get("aws", "expected_bucket_owner").strip()

    return {"ExpectedBucketOwner": owner} if owner else {}
