use actix_web::dev::{Service, ServiceRequest, ServiceResponse, Transform};
use actix_web::Error;
use futures_util::future::{ok, LocalBoxFuture, Ready};
use std::task::{Context, Poll};
use uuid::Uuid;

use lazy_static::lazy_static;
use prometheus::{
    register_histogram_vec, register_int_counter_vec, Encoder, HistogramVec, IntCounterVec,
    TextEncoder,
};

lazy_static! {
    pub static ref HTTP_REQUESTS_TOTAL: IntCounterVec = register_int_counter_vec!(
        "http_requests_total",
        "Total HTTP requests",
        &["method", "path", "status"]
    )
    .expect("metric can be created");
    pub static ref HTTP_REQUEST_DURATION: HistogramVec = register_histogram_vec!(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        &["method", "path"],
        vec![0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
    )
    .expect("metric can be created");
}

pub fn render_metrics() -> String {
    let encoder = TextEncoder::new();
    let metric_families = prometheus::gather();
    let mut buffer = Vec::new();
    encoder.encode(&metric_families, &mut buffer).unwrap();
    String::from_utf8(buffer).unwrap()
}

// -- Request ID Middleware --

pub struct RequestId;

impl<S, B> Transform<S, ServiceRequest> for RequestId
where
    S: Service<ServiceRequest, Response = ServiceResponse<B>, Error = Error>,
    S::Future: 'static,
    B: 'static,
{
    type Response = ServiceResponse<B>;
    type Error = Error;
    type Transform = RequestIdMiddleware<S>;
    type InitError = ();
    type Future = Ready<Result<Self::Transform, Self::InitError>>;

    fn new_transform(&self, service: S) -> Self::Future {
        ok(RequestIdMiddleware { service })
    }
}

pub struct RequestIdMiddleware<S> {
    service: S,
}

impl<S, B> Service<ServiceRequest> for RequestIdMiddleware<S>
where
    S: Service<ServiceRequest, Response = ServiceResponse<B>, Error = Error>,
    S::Future: 'static,
    B: 'static,
{
    type Response = ServiceResponse<B>;
    type Error = Error;
    type Future = LocalBoxFuture<'static, Result<Self::Response, Self::Error>>;

    fn poll_ready(&self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.service.poll_ready(cx)
    }

    fn call(&self, req: ServiceRequest) -> Self::Future {
        let request_id = req
            .headers()
            .get("x-request-id")
            .and_then(|v| v.to_str().ok())
            .map(|s| s.to_string())
            .unwrap_or_else(|| Uuid::new_v4().to_string());

        let method = req.method().to_string();
        let path = req
            .match_pattern()
            .unwrap_or_else(|| "unmatched".to_string());
        let start = std::time::Instant::now();

        let fut = self.service.call(req);

        Box::pin(async move {
            let res = fut.await?;
            let elapsed = start.elapsed().as_secs_f64();
            let status = res.status().as_u16().to_string();

            HTTP_REQUESTS_TOTAL
                .with_label_values(&[&method, &path, &status])
                .inc();
            HTTP_REQUEST_DURATION
                .with_label_values(&[&method, &path])
                .observe(elapsed);

            tracing::info!(
                request_id = %request_id,
                method = %method,
                path = %path,
                status = %status,
                duration_ms = %(elapsed * 1000.0),
                "Request completed"
            );

            Ok(res)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actix_web::http::header::{HeaderName, HeaderValue};
    use actix_web::http::StatusCode;
    use actix_web::{test as actix_test, web, App, HttpRequest, HttpResponse};

    /// Echoes back whatever identity the request claims, so a test can see
    /// exactly what the service would act on. Nothing in file-service
    /// authenticates a request before a handler runs.
    async fn echo_identity(req: HttpRequest) -> HttpResponse {
        let user = req
            .headers()
            .get("X-User-ID")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("<none>")
            .to_string();
        let authorization = req
            .headers()
            .get("Authorization")
            .and_then(|v| v.to_str().ok())
            .unwrap_or("<none>")
            .to_string();
        HttpResponse::Ok().json(serde_json::json!({
            "user": user,
            "authorization": authorization,
        }))
    }

    /// A route whose `match_pattern` is unique per test, so the metric label
    /// values a test asserts on can never be touched by another test running
    /// concurrently against the process-wide Prometheus registry.
    async fn call_through_middleware(
        route: &str,
        headers: &[(&str, &str)],
    ) -> (StatusCode, serde_json::Value) {
        let app = actix_test::init_service(
            App::new()
                .wrap(RequestId)
                .route(route, web::get().to(echo_identity)),
        )
        .await;

        let mut req = actix_test::TestRequest::get().uri(route);
        for (name, value) in headers {
            req = req.insert_header((*name, *value));
        }
        let resp = actix_test::call_service(&app, req.to_request()).await;
        let status = resp.status();
        let body = actix_test::read_body(resp).await;
        (status, serde_json::from_slice(&body).unwrap())
    }

    fn counter_value(method: &str, path: &str, status: &str) -> u64 {
        HTTP_REQUESTS_TOTAL
            .with_label_values(&[method, path, status])
            .get()
    }

    // -- Request id propagation --

    #[actix_rt::test]
    async fn request_id_middleware_passes_a_supplied_request_id_through() {
        let (status, _) = call_through_middleware(
            "/mw/request-id-supplied",
            &[("x-request-id", "11111111-2222-3333-4444-555555555555")],
        )
        .await;
        assert_eq!(status, StatusCode::OK);
    }

    #[actix_rt::test]
    async fn request_id_middleware_tolerates_a_missing_or_unusable_request_id() {
        // Absent, empty, non-UUID and non-ASCII values must all be handled by
        // falling back to a generated id rather than failing the request.
        let (status, _) = call_through_middleware("/mw/request-id-absent", &[]).await;
        assert_eq!(status, StatusCode::OK);

        for value in ["", "   ", "not-a-uuid", "../../etc/passwd", "a\tb"] {
            let (status, _) =
                call_through_middleware("/mw/request-id-odd", &[("x-request-id", value)]).await;
            assert_eq!(status, StatusCode::OK, "x-request-id={value:?} failed");
        }
    }

    #[actix_rt::test]
    async fn request_id_middleware_tolerates_a_non_utf8_request_id() {
        let app = actix_test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/mw/request-id-binary", web::get().to(echo_identity)),
        )
        .await;
        let req = actix_test::TestRequest::get()
            .uri("/mw/request-id-binary")
            .insert_header((
                HeaderName::from_static("x-request-id"),
                HeaderValue::from_bytes(&[0xff, 0xfe, 0x00_u8.wrapping_add(65)]).unwrap(),
            ))
            .to_request();
        let resp = actix_test::call_service(&app, req).await;
        assert_eq!(resp.status(), StatusCode::OK);
    }

    // -- Metrics --

    #[actix_rt::test]
    async fn middleware_counts_each_request_under_its_matched_route() {
        let route = "/mw/metrics-counted";
        let before = counter_value("GET", route, "200");
        call_through_middleware(route, &[]).await;
        call_through_middleware(route, &[]).await;
        assert_eq!(
            counter_value("GET", route, "200"),
            before + 2,
            "each request through the middleware increments its own label set"
        );
    }

    #[actix_rt::test]
    async fn unmatched_routes_are_bucketed_under_a_single_label() {
        // Without this, a 404 scan would create unbounded label cardinality.
        let app = actix_test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/mw/unmatched-known", web::get().to(echo_identity)),
        )
        .await;
        let before = counter_value("GET", "unmatched", "404");
        let req = actix_test::TestRequest::get()
            .uri("/mw/definitely-not-a-route")
            .to_request();
        let resp = actix_test::call_service(&app, req).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(counter_value("GET", "unmatched", "404"), before + 1);
    }

    #[actix_rt::test]
    async fn error_statuses_are_recorded_under_their_own_label() {
        async fn boom() -> HttpResponse {
            HttpResponse::InternalServerError().finish()
        }
        let route = "/mw/metrics-error";
        let app =
            actix_test::init_service(App::new().wrap(RequestId).route(route, web::get().to(boom)))
                .await;
        let before = counter_value("GET", route, "500");
        let resp =
            actix_test::call_service(&app, actix_test::TestRequest::get().uri(route).to_request())
                .await;
        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(counter_value("GET", route, "500"), before + 1);
    }

    #[test]
    fn render_metrics_emits_the_prometheus_text_exposition_format() {
        HTTP_REQUESTS_TOTAL
            .with_label_values(&["GET", "/mw/render", "200"])
            .inc();
        HTTP_REQUEST_DURATION
            .with_label_values(&["GET", "/mw/render"])
            .observe(0.01);

        let rendered = render_metrics();
        assert!(rendered.contains("# TYPE http_requests_total counter"));
        assert!(rendered.contains("http_requests_total{"));
        assert!(rendered.contains("# TYPE http_request_duration_seconds histogram"));
        assert!(rendered.contains("http_request_duration_seconds_bucket{"));
        assert!(rendered.contains("path=\"/mw/render\""));
    }

    #[test]
    fn request_duration_histogram_uses_the_declared_buckets() {
        HTTP_REQUEST_DURATION
            .with_label_values(&["GET", "/mw/buckets"])
            .observe(0.0);
        let rendered = render_metrics();
        for bucket in [
            "0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1", "2.5", "5",
        ] {
            assert!(
                rendered.contains(&format!("le=\"{bucket}\"")),
                "bucket {bucket} missing from the exposition output"
            );
        }
    }

    // -- Authentication / identity headers --
    //
    // file-service mounts exactly one middleware (`RequestId`). There is no
    // authentication layer here: the api-gateway is expected to validate the
    // JWT and inject `X-User-ID`. The tests below pin down what that means for
    // a request that reaches this service directly.

    #[actix_rt::test]
    async fn a_request_with_no_authorization_header_is_served() {
        let (status, body) = call_through_middleware("/mw/auth-absent", &[]).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["authorization"], "<none>");
        assert_eq!(body["user"], "<none>");
    }

    #[actix_rt::test]
    async fn authorization_headers_are_never_inspected() {
        // Present, malformed, wrong scheme and empty-subject tokens are all
        // treated identically: the middleware chain does not look at them.
        for authorization in [
            "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ.sig",
            "Bearer",
            "Bearer ",
            "Bearer not.a.jwt",
            "Basic dXNlcjpwYXNz",
            "Token abc123",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIifQ.sig",
            "",
        ] {
            let (status, body) =
                call_through_middleware("/mw/auth-variants", &[("Authorization", authorization)])
                    .await;
            assert_eq!(
                status,
                StatusCode::OK,
                "Authorization={authorization:?} was not rejected"
            );
            assert_eq!(body["authorization"], authorization);
        }
    }

    #[actix_rt::test]
    async fn a_client_supplied_user_id_header_is_trusted_verbatim() {
        // Documents current behavior: `X-User-ID` is taken at face value with
        // no signature, no gateway attestation and no cross-check against an
        // Authorization header. See FINDING in
        // client_supplied_user_id_should_not_be_trusted_without_a_gateway_attestation.
        let victim = "11111111-1111-1111-1111-111111111111";
        let (status, body) = call_through_middleware(
            "/mw/spoofed-identity",
            &[("X-User-ID", victim), ("Authorization", "Bearer nonsense")],
        )
        .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(
            body["user"], victim,
            "the handler receives whatever user id the caller claimed"
        );
    }

    #[actix_rt::test]
    async fn a_malformed_or_empty_user_id_header_is_passed_through_unvalidated() {
        for value in ["", "   ", "not-a-uuid", "0", "*", "' OR 1=1 --"] {
            let (status, body) =
                call_through_middleware("/mw/identity-malformed", &[("X-User-ID", value)]).await;
            assert_eq!(status, StatusCode::OK, "X-User-ID={value:?} was rejected");
            assert_eq!(
                body["user"], value,
                "the middleware neither validates nor normalizes X-User-ID"
            );
        }
    }

    #[actix_rt::test]
    #[ignore = "FINDING: file-service trusts the client-supplied X-User-ID header. Requests that \
                reach the service directly (in-cluster, or if an ingress rule ever exposes port \
                8082) can impersonate any user by setting the header; there is no auth \
                middleware and no check that the header was injected by the api-gateway"]
    async fn client_supplied_user_id_should_not_be_trusted_without_a_gateway_attestation() {
        let (status, _) = call_through_middleware(
            "/mw/spoof-rejected",
            &[("X-User-ID", "11111111-1111-1111-1111-111111111111")],
        )
        .await;
        assert_eq!(
            status,
            StatusCode::UNAUTHORIZED,
            "an unauthenticated request carrying only X-User-ID must not be served"
        );
    }

    #[actix_rt::test]
    #[ignore = "FINDING: no middleware rejects a request without credentials; every route is \
                reachable unauthenticated when the service is called directly"]
    async fn requests_without_credentials_should_be_rejected() {
        let (status, _) = call_through_middleware("/mw/anonymous", &[]).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
    }
}
