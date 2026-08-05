# Runbook: File Upload Failures

**Severity:** High

## Alert

`FileUploadHighErrorRate` -- fires when file-service 5xx rate exceeds 10% over a 1-minute window.

## Symptoms

- Users cannot upload files; the UI shows generic upload error messages.
- The Chaos Scenarios dashboard shows elevated error rates on the file-service panel.
- Application logs contain `NoSuchBucket` errors from the AWS S3 SDK.

## Investigation Steps

1. Confirm the error in file-service logs:
   ```
   kubectl logs -l app=file-service --tail=100 -n otterworks | grep -i "NoSuchBucket\|S3\|500"
   ```
2. Check whether the chaos flag `chaos:file-service:upload_s3_error` is set in Redis:
   ```
   redis-cli EXISTS chaos:file-service:upload_s3_error
   ```
   In a tenant namespace, run it against the tenant's own Redis:
   ```
   kubectl -n otterworks-<TENANT> exec deploy/redis -- redis-cli EXISTS chaos:file-service:upload_s3_error
   ```
   When this flag is set, `upload_file` in
   `services/file-service/src/handlers.rs` redirects uploads to the
   nonexistent bucket `otterworks-files-chaos-nonexistent`, producing
   `NoSuchBucket` 5xx errors (the `file-upload-fails` scenario in
   `scripts/bug-catalog.yaml`). Downloads and metadata reads are unaffected.
3. If the flag is not set, check for a config-override injection
   (`file-bad-bucket` scenario) or a genuine misconfiguration of the
   `S3_BUCKET` env var:
   ```
   kubectl -n otterworks-<TENANT> get deploy file-service \
     -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="S3_BUCKET")]}'
   ```
   The expected value is the `s3_file_bucket` Terraform output
   (`otterworks-files-dev` in the dev account). Verify the bucket exists:
   ```
   aws s3api head-bucket --bucket <value>
   ```
4. Review recent Helm releases for the tenant to spot a recent override:
   ```
   helm -n otterworks-<TENANT> history file-service
   ```

## Resolution Steps

- **Chaos flag set** (most common in demos): clear it — the flag also
  auto-expires after its TTL.
  ```
  ./scripts/inject-bug.sh <TENANT> reset
  ```
  or directly:
  ```
  kubectl -n otterworks-<TENANT> exec deploy/redis -- redis-cli DEL chaos:file-service:upload_s3_error
  ```
  No redeploy is needed; errors stop immediately.
- **Bad `S3_BUCKET` override**: redeploy the tenant with the correct value
  (rewires config from Terraform outputs):
  ```
  ./scripts/deploy-tenant.sh <TENANT>
  ```
- **Bucket genuinely missing**: restore it via Terraform
  (`infrastructure/terraform`), never by hand-creating the bucket.

Confirm recovery: upload a file through the web app, or watch the
`FileUploadHighErrorRate` panel on the Chaos Scenarios dashboard return to
zero, and check file-service logs for `Uploaded object to S3`.

## Post-Incident

- Verify the alert has resolved in Grafana.
- If the cause was an injected scenario, note which tenant and scenario in
  the demo log so the next operator has context.
- If the cause was a real misconfiguration, capture how the bad `S3_BUCKET`
  value shipped (Helm override, Terraform drift) and add a guard to the
  deploy script if applicable.
