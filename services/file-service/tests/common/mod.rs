//! Shared test harness for the file-service integration tests.
//!
//! Everything here is offline: AWS calls go to an in-process mock HTTP server and the
//! chaos-flag lookup goes to an in-process RESP (Redis) stub. No LocalStack, no docker,
//! no wall-clock waits, no shared global state — every helper is per-test.
#![allow(dead_code)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpListener;
use uuid::Uuid;

// ── Mock AWS (S3 + DynamoDB over plain HTTP) ───────────────────────────

#[derive(Clone, Debug)]
pub struct RecordedRequest {
    pub method: String,
    pub path: String,
    /// `x-amz-target` header: present for DynamoDB (JSON 1.0) calls only.
    pub target: Option<String>,
    pub headers: HashMap<String, String>,
    pub body: Vec<u8>,
}

impl RecordedRequest {
    pub fn is_dynamo(&self, op: &str) -> bool {
        self.target.as_deref() == Some(format!("DynamoDB_20120810.{op}").as_str())
    }

    /// Request path with the SDK's `?x-id=...` operation hint stripped.
    pub fn path_only(&self) -> &str {
        self.path.split('?').next().unwrap_or(&self.path)
    }

    pub fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap_or(Value::Null)
    }

    pub fn table_name(&self) -> Option<String> {
        self.json()
            .get("TableName")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
    }
}

pub struct MockResponse {
    pub status: u16,
    pub content_type: &'static str,
    pub body: String,
}

pub type Responder = Arc<dyn Fn(&RecordedRequest, usize) -> MockResponse + Send + Sync>;

pub fn json_response(status: u16, body: impl Into<String>) -> MockResponse {
    MockResponse {
        status,
        content_type: "application/x-amz-json-1.0",
        body: body.into(),
    }
}

pub fn xml_response(status: u16, body: impl Into<String>) -> MockResponse {
    MockResponse {
        status,
        content_type: "application/xml",
        body: body.into(),
    }
}

pub fn s3_ok() -> MockResponse {
    xml_response(200, "")
}

pub fn s3_error(status: u16, code: &str) -> MockResponse {
    xml_response(
        status,
        format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Error><Code>{code}</Code>\
             <Message>{code} raised by the mock</Message><RequestId>mock</RequestId></Error>"
        ),
    )
}

pub fn dynamo_error(status: u16, code: &str) -> MockResponse {
    json_response(
        status,
        json!({
            "__type": format!("com.amazonaws.dynamodb.v20120810#{code}"),
            "message": format!("{code} raised by the mock"),
        })
        .to_string(),
    )
}

/// An in-process HTTP server standing in for S3 and DynamoDB.
pub struct MockAws {
    pub addr: SocketAddr,
    requests: Arc<Mutex<Vec<RecordedRequest>>>,
    handle: tokio::task::JoinHandle<()>,
}

impl Drop for MockAws {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

impl MockAws {
    pub async fn start(responder: Responder) -> Self {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.expect("bind");
        let addr = listener.local_addr().expect("local_addr");
        let requests: Arc<Mutex<Vec<RecordedRequest>>> = Arc::new(Mutex::new(Vec::new()));
        let seen = Arc::new(AtomicUsize::new(0));

        let recorded = requests.clone();
        let handle = tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    return;
                };
                let responder = responder.clone();
                let recorded = recorded.clone();
                let seen = seen.clone();
                tokio::spawn(async move {
                    serve_connection(stream, responder, recorded, seen).await;
                });
            }
        });

        Self {
            addr,
            requests,
            handle,
        }
    }

    pub fn endpoint(&self) -> String {
        format!("http://{}", self.addr)
    }

    pub fn requests(&self) -> Vec<RecordedRequest> {
        self.requests.lock().expect("requests lock").clone()
    }

    pub fn dynamo_calls(&self, op: &str) -> Vec<RecordedRequest> {
        self.requests()
            .into_iter()
            .filter(|r| r.is_dynamo(op))
            .collect()
    }

    /// S3 calls are the ones without a DynamoDB `x-amz-target` header.
    pub fn s3_calls(&self, method: &str) -> Vec<RecordedRequest> {
        self.requests()
            .into_iter()
            .filter(|r| r.target.is_none() && r.method == method)
            .collect()
    }
}

async fn serve_connection(
    stream: tokio::net::TcpStream,
    responder: Responder,
    recorded: Arc<Mutex<Vec<RecordedRequest>>>,
    seen: Arc<AtomicUsize>,
) {
    let mut reader = BufReader::new(stream);

    loop {
        let mut request_line = String::new();
        match reader.read_line(&mut request_line).await {
            Ok(0) | Err(_) => return,
            Ok(_) => {}
        }
        if request_line.trim().is_empty() {
            continue;
        }
        let mut parts = request_line.split_whitespace();
        let method = parts.next().unwrap_or_default().to_string();
        let path = parts.next().unwrap_or_default().to_string();

        let mut headers: HashMap<String, String> = HashMap::new();
        loop {
            let mut line = String::new();
            match reader.read_line(&mut line).await {
                Ok(0) | Err(_) => return,
                Ok(_) => {}
            }
            let line = line.trim_end_matches(['\r', '\n']);
            if line.is_empty() {
                break;
            }
            if let Some((k, v)) = line.split_once(':') {
                headers.insert(k.trim().to_lowercase(), v.trim().to_string());
            }
        }

        let body = match read_body(&mut reader, &headers).await {
            Some(body) => body,
            None => return,
        };

        let request = RecordedRequest {
            method,
            path,
            target: headers.get("x-amz-target").cloned(),
            headers,
            body,
        };
        recorded
            .lock()
            .expect("requests lock")
            .push(request.clone());
        let index = seen.fetch_add(1, Ordering::SeqCst);

        let response = responder(&request, index);
        let payload = format!(
            "HTTP/1.1 {} {}\r\ncontent-type: {}\r\ncontent-length: {}\r\nconnection: keep-alive\r\n\r\n{}",
            response.status,
            reason_phrase(response.status),
            response.content_type,
            response.body.len(),
            response.body,
        );
        if reader
            .get_mut()
            .write_all(payload.as_bytes())
            .await
            .is_err()
        {
            return;
        }
    }
}

async fn read_body(
    reader: &mut BufReader<tokio::net::TcpStream>,
    headers: &HashMap<String, String>,
) -> Option<Vec<u8>> {
    if let Some(len) = headers.get("content-length").and_then(|v| v.parse().ok()) {
        let mut body = vec![0u8; len];
        reader.read_exact(&mut body).await.ok()?;
        return Some(body);
    }
    if headers
        .get("transfer-encoding")
        .map(|v| v.contains("chunked"))
        .unwrap_or(false)
    {
        let mut body = Vec::new();
        loop {
            let mut size_line = String::new();
            reader.read_line(&mut size_line).await.ok()?;
            let size =
                usize::from_str_radix(size_line.trim().split(';').next().unwrap_or("0").trim(), 16)
                    .ok()?;
            if size == 0 {
                // consume trailers up to the terminating blank line
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).await.ok()? == 0 {
                        break;
                    }
                    if line.trim().is_empty() {
                        break;
                    }
                }
                break;
            }
            let mut chunk = vec![0u8; size + 2];
            reader.read_exact(&mut chunk).await.ok()?;
            chunk.truncate(size);
            body.extend_from_slice(&chunk);
        }
        return Some(body);
    }
    Some(Vec::new())
}

fn reason_phrase(status: u16) -> &'static str {
    match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        409 => "Conflict",
        500 => "Internal Server Error",
        503 => "Service Unavailable",
        _ => "Status",
    }
}

// ── In-memory DynamoDB / S3 state ──────────────────────────────────────

/// Minimal stand-in for the four DynamoDB tables the service uses. `PutItem` replaces by
/// primary key and `Query` honours the key condition and sort direction, so tests see the
/// same shape of data a real table would return.
///
/// `Scan` filter expressions are intentionally *not* evaluated: each test seeds only the
/// rows it cares about, so returning the whole table is both sufficient and predictable.
#[derive(Default)]
pub struct MockState {
    pub files: Vec<Value>,
    pub versions: Vec<Value>,
    pub shares: Vec<Value>,
    pub folders: Vec<Value>,
}

impl MockState {
    pub fn shared() -> Arc<Mutex<Self>> {
        Arc::new(Mutex::new(Self::default()))
    }

    fn table(&mut self, name: &str) -> &mut Vec<Value> {
        if name.contains("versions") {
            &mut self.versions
        } else if name.contains("shares") {
            &mut self.shares
        } else if name.contains("folders") {
            &mut self.folders
        } else {
            &mut self.files
        }
    }
}

pub const FILES_TABLE: &str = "test-file-metadata";
pub const FOLDERS_TABLE: &str = "test-folders";
pub const VERSIONS_TABLE: &str = "test-file-versions";
pub const SHARES_TABLE: &str = "test-file-shares";

/// A responder backed by [`MockState`]: happy-path S3 plus a tiny DynamoDB.
pub fn state_responder(state: Arc<Mutex<MockState>>) -> Responder {
    Arc::new(move |req, _index| {
        let mut state = state.lock().expect("state lock");
        match req.target.as_deref() {
            Some("DynamoDB_20120810.PutItem") => {
                let body = req.json();
                let table = body["TableName"].as_str().unwrap_or_default().to_string();
                let item = body["Item"].clone();
                let key = key_attrs(&table);
                let rows = state.table(&table);
                // DynamoDB PutItem replaces the row with the same primary key.
                match rows.iter_mut().find(|row| same_key(row, &item, key)) {
                    Some(existing) => *existing = item,
                    None => rows.push(item),
                }
                json_response(200, "{}")
            }
            Some("DynamoDB_20120810.GetItem") => {
                let body = req.json();
                let table = body["TableName"].as_str().unwrap_or_default().to_string();
                let wanted = body["Key"]["id"]["S"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string();
                let found = state
                    .table(&table)
                    .iter()
                    .find(|item| item["id"]["S"].as_str() == Some(wanted.as_str()))
                    .cloned();
                match found {
                    Some(item) => json_response(200, json!({ "Item": item }).to_string()),
                    None => json_response(200, "{}"),
                }
            }
            Some("DynamoDB_20120810.DeleteItem") => {
                let body = req.json();
                let table = body["TableName"].as_str().unwrap_or_default().to_string();
                let wanted = body["Key"]["id"]["S"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string();
                state
                    .table(&table)
                    .retain(|item| item["id"]["S"].as_str() != Some(wanted.as_str()));
                json_response(200, "{}")
            }
            Some("DynamoDB_20120810.UpdateItem") => {
                let body = req.json();
                let table = body["TableName"].as_str().unwrap_or_default().to_string();
                let wanted = body["Key"]["id"]["S"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string();
                let Some(item) = state
                    .table(&table)
                    .iter_mut()
                    .find(|item| item["id"]["S"].as_str() == Some(wanted.as_str()))
                else {
                    // every caller guards with `attribute_exists(id)`
                    return dynamo_error(400, "ConditionalCheckFailedException");
                };
                apply_update_expression(item, &body);
                json_response(200, "{}")
            }
            Some("DynamoDB_20120810.Query") => {
                let body = req.json();
                let table = body["TableName"].as_str().unwrap_or_default().to_string();
                let scanned = state.table(&table).len();
                let mut items = apply_key_condition(state.table(&table), &body);
                sort_by_range_key(&mut items, &table);
                if body["ScanIndexForward"] == json!(false) {
                    items.reverse();
                }
                json_response(
                    200,
                    json!({ "Items": items, "Count": items.len(), "ScannedCount": scanned })
                        .to_string(),
                )
            }
            Some("DynamoDB_20120810.Scan") => {
                let body = req.json();
                let table = body["TableName"].as_str().unwrap_or_default().to_string();
                let items = state.table(&table).clone();
                json_response(
                    200,
                    json!({ "Items": items, "Count": items.len(), "ScannedCount": items.len() })
                        .to_string(),
                )
            }
            Some(_) => json_response(200, "{}"),
            // No x-amz-target header ⇒ this is S3.
            None => s3_ok(),
        }
    })
}

/// The primary-key attributes of a table: `files`, `folders` and `shares` are keyed on
/// `id`, `versions` on the `(file_id, version)` pair.
fn key_attrs(table: &str) -> &'static [&'static str] {
    if table.contains("versions") {
        &["file_id", "version"]
    } else {
        &["id"]
    }
}

fn same_key(left: &Value, right: &Value, key: &[&str]) -> bool {
    key.iter()
        .all(|attr| !left[attr].is_null() && left[attr] == right[attr])
}

/// Keep only the rows matching the equality terms of a `KeyConditionExpression`
/// (`attr = :value`, joined by `AND`), the only form the service emits.
fn apply_key_condition(rows: &[Value], body: &Value) -> Vec<Value> {
    let condition = body["KeyConditionExpression"].as_str().unwrap_or_default();
    let names = &body["ExpressionAttributeNames"];
    let values = &body["ExpressionAttributeValues"];

    let terms: Vec<(String, &Value)> = condition
        .split("AND")
        .filter_map(|term| term.split_once('='))
        .filter_map(|(lhs, rhs)| {
            let (lhs, rhs) = (lhs.trim(), rhs.trim());
            let attr = match lhs.strip_prefix('#') {
                Some(_) => names[lhs].as_str().unwrap_or(lhs).to_string(),
                None => lhs.to_string(),
            };
            values.get(rhs).map(|value| (attr, value))
        })
        .collect();

    rows.iter()
        .filter(|row| terms.iter().all(|(attr, value)| &&row[attr] == value))
        .cloned()
        .collect()
}

/// Order rows by the table's range key, as DynamoDB does for a `Query`.
fn sort_by_range_key(rows: &mut [Value], table: &str) {
    if table == VERSIONS_TABLE {
        rows.sort_by_key(|row| {
            row["version"]["N"]
                .as_str()
                .and_then(|n| n.parse::<u64>().ok())
                .unwrap_or_default()
        });
    }
}

/// Apply the subset of `UpdateExpression` the service actually emits:
/// `SET <attr|#name> = :value[, ...] [REMOVE <attr>]`.
fn apply_update_expression(item: &mut Value, body: &Value) {
    let expression = body["UpdateExpression"].as_str().unwrap_or_default();
    let names = &body["ExpressionAttributeNames"];
    let values = &body["ExpressionAttributeValues"];

    let (set_part, remove_part) = match expression.find("REMOVE") {
        Some(at) => (
            &expression[..at],
            Some(expression[at + "REMOVE".len()..].trim()),
        ),
        None => (expression, None),
    };

    for assignment in set_part.trim_start_matches("SET").split(',') {
        let Some((lhs, rhs)) = assignment.split_once('=') else {
            continue;
        };
        let (lhs, rhs) = (lhs.trim(), rhs.trim());
        let attr = match lhs.strip_prefix('#') {
            Some(_) => names[lhs].as_str().unwrap_or(lhs).to_string(),
            None => lhs.to_string(),
        };
        if let Some(value) = values.get(rhs) {
            item[attr] = value.clone();
        }
    }

    if let Some(attr) = remove_part {
        if let Some(object) = item.as_object_mut() {
            object.remove(attr.trim());
        }
    }
}

/// Wrap a responder, overriding the response for requests matching `pred`.
pub fn override_when(
    inner: Responder,
    pred: impl Fn(&RecordedRequest) -> bool + Send + Sync + 'static,
    make: impl Fn() -> MockResponse + Send + Sync + 'static,
) -> Responder {
    Arc::new(
        move |req, index| {
            if pred(req) {
                make()
            } else {
                inner(req, index)
            }
        },
    )
}

// ── DynamoDB item builders ─────────────────────────────────────────────

pub fn s(value: impl Into<String>) -> Value {
    json!({ "S": value.into() })
}

pub fn n(value: impl ToString) -> Value {
    json!({ "N": value.to_string() })
}

pub fn b(value: bool) -> Value {
    json!({ "BOOL": value })
}

/// A file row as `put_file` would have written it.
pub fn file_item(id: Uuid, owner: Uuid, name: &str, size: u64, trashed: bool) -> Value {
    let ts = "2026-01-01T00:00:00+00:00";
    json!({
        "id": s(id.to_string()),
        "name": s(name),
        "mime_type": s("text/plain"),
        "size_bytes": n(size),
        "s3_key": s(format!("files/{owner}/{id}")),
        "owner_id": s(owner.to_string()),
        "version": n(1),
        "is_trashed": b(trashed),
        "created_at": s(ts),
        "updated_at": s(ts),
    })
}

/// A file row that also carries a `folder_id`.
pub fn file_item_in_folder(
    id: Uuid,
    owner: Uuid,
    name: &str,
    folder: Uuid,
    trashed: bool,
) -> Value {
    let mut item = file_item(id, owner, name, 1, trashed);
    item["folder_id"] = s(folder.to_string());
    item
}

pub fn share_item(
    id: Uuid,
    file_id: Uuid,
    shared_with: Uuid,
    shared_by: Uuid,
    permission: &str,
) -> Value {
    json!({
        "id": s(id.to_string()),
        "file_id": s(file_id.to_string()),
        "shared_with": s(shared_with.to_string()),
        "permission": s(permission),
        "shared_by": s(shared_by.to_string()),
        "created_at": s("2026-01-02T00:00:00+00:00"),
    })
}

pub fn folder_item(id: Uuid, owner: Uuid, name: &str, parent: Option<Uuid>) -> Value {
    let mut item = json!({
        "id": s(id.to_string()),
        "name": s(name),
        "owner_id": s(owner.to_string()),
        "created_at": s("2026-01-01T00:00:00+00:00"),
        "updated_at": s("2026-01-01T00:00:00+00:00"),
    });
    if let Some(parent) = parent {
        item["parent_id"] = s(parent.to_string());
    }
    item
}

pub fn version_item(file_id: Uuid, owner: Uuid, version: u32, size: u64) -> Value {
    json!({
        "file_id": s(file_id.to_string()),
        "version": n(version),
        "s3_key": s(format!("files/{owner}/{file_id}/v{version}")),
        "size_bytes": n(size),
        "created_by": s(owner.to_string()),
        "created_at": s(format!("2026-01-0{}T00:00:00+00:00", version.min(9))),
    })
}

// ── Fake Redis (chaos flag lookup) ─────────────────────────────────────

/// A RESP stub that answers `EXISTS` with a fixed value and `+OK` to anything else.
pub struct FakeRedis {
    pub addr: SocketAddr,
    handle: tokio::task::JoinHandle<()>,
}

impl Drop for FakeRedis {
    fn drop(&mut self) {
        self.handle.abort();
    }
}

impl FakeRedis {
    pub async fn start(chaos_flag_set: bool) -> Self {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.expect("bind");
        let addr = listener.local_addr().expect("local_addr");
        let handle = tokio::spawn(async move {
            loop {
                let Ok((stream, _)) = listener.accept().await else {
                    return;
                };
                tokio::spawn(async move {
                    let mut reader = BufReader::new(stream);
                    while let Some(command) = read_resp_command(&mut reader).await {
                        let reply = match command.first().map(|c| c.to_uppercase()) {
                            Some(cmd) if cmd == "EXISTS" => {
                                if chaos_flag_set {
                                    ":1\r\n".to_string()
                                } else {
                                    ":0\r\n".to_string()
                                }
                            }
                            _ => "+OK\r\n".to_string(),
                        };
                        if reader.get_mut().write_all(reply.as_bytes()).await.is_err() {
                            return;
                        }
                    }
                });
            }
        });
        Self { addr, handle }
    }

    pub fn url(&self) -> String {
        format!("redis://{}", self.addr)
    }

    pub async fn connection_manager(&self) -> redis::aio::ConnectionManager {
        let client = redis::Client::open(self.url()).expect("redis client");
        redis::aio::ConnectionManager::new(client)
            .await
            .expect("connect to fake redis")
    }
}

async fn read_resp_command(reader: &mut BufReader<tokio::net::TcpStream>) -> Option<Vec<String>> {
    let mut header = String::new();
    if reader.read_line(&mut header).await.ok()? == 0 {
        return None;
    }
    let header = header.trim();
    let count: usize = header.strip_prefix('*')?.parse().ok()?;
    let mut args = Vec::with_capacity(count);
    for _ in 0..count {
        let mut len_line = String::new();
        if reader.read_line(&mut len_line).await.ok()? == 0 {
            return None;
        }
        let len: usize = len_line.trim().strip_prefix('$')?.parse().ok()?;
        let mut buf = vec![0u8; len + 2];
        reader.read_exact(&mut buf).await.ok()?;
        buf.truncate(len);
        args.push(String::from_utf8_lossy(&buf).to_string());
    }
    Some(args)
}

// ── Multipart body builder ─────────────────────────────────────────────

pub const BOUNDARY: &str = "otterworksTESTboundary";

pub struct Part {
    pub name: &'static str,
    pub filename: Option<String>,
    pub content_type: Option<&'static str>,
    pub data: Vec<u8>,
}

impl Part {
    pub fn file(filename: impl Into<String>, content_type: &'static str, data: Vec<u8>) -> Self {
        Self {
            name: "file",
            filename: Some(filename.into()),
            content_type: Some(content_type),
            data,
        }
    }

    pub fn field(name: &'static str, value: impl Into<String>) -> Self {
        Self {
            name,
            filename: None,
            content_type: None,
            data: value.into().into_bytes(),
        }
    }
}

pub fn multipart_content_type() -> String {
    format!("multipart/form-data; boundary={BOUNDARY}")
}

pub fn multipart_body(parts: Vec<Part>) -> Vec<u8> {
    let mut body = Vec::new();
    for part in parts {
        body.extend_from_slice(format!("--{BOUNDARY}\r\n").as_bytes());
        let disposition = match &part.filename {
            Some(filename) => format!(
                "content-disposition: form-data; name=\"{}\"; filename=\"{}\"\r\n",
                part.name, filename
            ),
            None => format!("content-disposition: form-data; name=\"{}\"\r\n", part.name),
        };
        body.extend_from_slice(disposition.as_bytes());
        if let Some(ct) = part.content_type {
            body.extend_from_slice(format!("content-type: {ct}\r\n").as_bytes());
        }
        body.extend_from_slice(b"\r\n");
        body.extend_from_slice(&part.data);
        body.extend_from_slice(b"\r\n");
    }
    body.extend_from_slice(format!("--{BOUNDARY}--\r\n").as_bytes());
    body
}

// ── AWS SDK clients pointed at the mock ────────────────────────────────

fn static_credentials() -> aws_sdk_s3::config::Credentials {
    aws_sdk_s3::config::Credentials::new("test-access-key", "test-secret-key", None, None, "tests")
}

/// An S3 client that talks to the mock endpoint, signs with static credentials and never
/// retries (retries would add exponential-backoff sleeps and duplicate recorded requests).
pub fn s3_sdk_client(endpoint: &str) -> aws_sdk_s3::Client {
    let conf = aws_sdk_s3::config::Builder::new()
        .behavior_version(aws_sdk_s3::config::BehaviorVersion::latest())
        .region(aws_sdk_s3::config::Region::new("us-east-1"))
        .credentials_provider(static_credentials())
        .endpoint_url(endpoint)
        .force_path_style(true)
        .retry_config(aws_sdk_s3::config::retry::RetryConfig::disabled())
        .build();
    aws_sdk_s3::Client::from_conf(conf)
}

pub fn dynamodb_sdk_client(endpoint: &str) -> aws_sdk_dynamodb::Client {
    let conf = aws_sdk_dynamodb::config::Builder::new()
        .behavior_version(aws_sdk_dynamodb::config::BehaviorVersion::latest())
        .region(aws_sdk_dynamodb::config::Region::new("us-east-1"))
        .credentials_provider(aws_sdk_dynamodb::config::Credentials::new(
            "test-access-key",
            "test-secret-key",
            None,
            None,
            "tests",
        ))
        .endpoint_url(endpoint)
        .retry_config(aws_sdk_dynamodb::config::retry::RetryConfig::disabled())
        .build();
    aws_sdk_dynamodb::Client::from_conf(conf)
}

pub const TEST_BUCKET: &str = "test-bucket";
