# File Service

## Build and test

The default unit-test tier is infrastructure-free and is the fast CI gate:

```sh
cargo fmt --check
cargo clippy -- -D warnings
cargo test
```

The LocalStack integration tier runs as a separate CI job so it does not slow
the fast gate.

## LocalStack integration tests

The integration tests are disabled unless the `integration` Cargo feature is
enabled. They require LocalStack on `http://localhost:4566` and Redis on
`localhost:6379`, because the request handlers use Redis for their chaos-flag
connection.

Start the required infrastructure from the repository root:

```sh
docker compose -f docker-compose.infra.yml up -d localstack redis
```

Then run the integration tier from this directory:

```sh
cargo test --features integration
```

`AWS_ENDPOINT_URL`, `S3_BUCKET`, and the DynamoDB table environment variables
can be overridden when testing against a different LocalStack setup.
