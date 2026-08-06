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

/// WP-02 — request-id handling, metrics recording, and the auth headers the
/// middleware chain does *not* police.
///
/// Note on scope: the work package asks for auth-header extraction tests
/// (missing header, malformed bearer prefix, untrusted client-supplied user
/// id). No such extractor exists - `middleware.rs` implements only request-id
/// and metrics, and the identity is read straight out of `X-User-ID` by the
/// handlers. The cases below therefore drive the real middleware chain with a
/// probe endpoint and pin what actually happens to those headers today; the
/// intended behaviour is written down in the ignored test at the end.
#[cfg(test)]
mod request_id_and_auth_tests {
    use super::*;
    use actix_web::http::header::{HeaderName, HeaderValue};
    use actix_web::http::StatusCode;
    use actix_web::{test, web, App, HttpRequest, HttpResponse};

    /// Stand-in for a protected endpoint. Reports the identity the service
    /// would act on and whatever credential arrived with the request.
    async fn identity_probe(req: HttpRequest) -> HttpResponse {
        let header = |name: &str| {
            req.headers()
                .get(name)
                .and_then(|value| value.to_str().ok())
                .unwrap_or("<absent>")
                .to_string()
        };

        HttpResponse::Ok().json(serde_json::json!({
            "user_id": header("x-user-id"),
            "authorization": header("authorization"),
        }))
    }

    async fn failing_probe() -> Result<HttpResponse, crate::errors::ServiceError> {
        Err(crate::errors::ServiceError::Internal("probe".into()))
    }

    fn counter(method: &str, path: &str, status: &str) -> u64 {
        HTTP_REQUESTS_TOTAL
            .get_metric_with_label_values(&[method, path, status])
            .expect("counter series")
            .get()
    }

    fn duration_samples(method: &str, path: &str) -> u64 {
        HTTP_REQUEST_DURATION
            .get_metric_with_label_values(&[method, path])
            .expect("histogram series")
            .get_sample_count()
    }

    // -- Auth headers --

    /// WP-02 finding F10 (genuine, pinned not fixed).
    ///
    /// Nothing in the middleware chain looks at `Authorization`. A request
    /// with no credential at all is passed straight through to the handler,
    /// so the service is safe only for as long as an upstream gateway is the
    /// sole route to it.
    #[actix_web::test]
    async fn a_request_with_no_authorization_header_still_reaches_the_handler() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/auth/absent", web::get().to(identity_probe)),
        )
        .await;

        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/auth/absent")
                .to_request(),
        )
        .await;

        assert_eq!(
            response.status(),
            StatusCode::OK,
            "an unauthenticated request is not rejected here"
        );
        let body: serde_json::Value = test::read_body_json(response).await;
        assert_eq!(body["authorization"], "<absent>");
    }

    /// Malformed credentials are equally unexamined: none of these shapes is
    /// distinguished from a valid bearer token, because none of them is
    /// parsed.
    #[actix_web::test]
    async fn malformed_bearer_credentials_are_passed_through_unexamined() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/auth/malformed", web::get().to(identity_probe)),
        )
        .await;

        let credentials = [
            "",
            "Bearer",
            "Bearer ",
            "Bearertoken",
            "bearer lowercase-scheme",
            "Basic dXNlcjpwYXNzd29yZA==",
            "Token abc123",
            "Bearer not.a.jwt",
            "Bearer  double-space",
        ];

        for credential in credentials {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri("/wp02/auth/malformed")
                    .insert_header(("authorization", credential))
                    .to_request(),
            )
            .await;

            assert_eq!(
                response.status(),
                StatusCode::OK,
                "credential {credential:?} was not rejected"
            );
            let body: serde_json::Value = test::read_body_json(response).await;
            assert_eq!(
                body["authorization"], credential,
                "credential {credential:?} was altered in flight"
            );
        }
    }

    /// Same finding, from the other direction: the caller's identity is a
    /// plain client-settable header that arrives at the handler verbatim, and
    /// is never cross-checked against a credential. Anyone who can reach the
    /// pod directly can act as any user by setting one header.
    #[actix_web::test]
    async fn a_client_supplied_user_id_reaches_the_handler_verbatim() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/auth/spoof", web::get().to(identity_probe)),
        )
        .await;

        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/auth/spoof")
                .insert_header(("authorization", "Bearer token-for-alice"))
                .insert_header(("x-user-id", "00000000-0000-0000-0000-0000000000ff"))
                .to_request(),
        )
        .await;

        let body: serde_json::Value = test::read_body_json(response).await;
        assert_eq!(
            body["user_id"], "00000000-0000-0000-0000-0000000000ff",
            "the spoofed identity is neither dropped nor overwritten"
        );
    }

    #[actix_web::test]
    #[ignore = "expected-fail, WP-02 finding F10: no authentication is enforced in-process"]
    async fn unauthenticated_and_spoofed_requests_should_be_refused() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/auth/intended", web::get().to(identity_probe)),
        )
        .await;

        for credential in [None, Some("Bearer"), Some("Basic dXNlcjpwYXNz")] {
            let mut request = test::TestRequest::get().uri("/wp02/auth/intended");
            if let Some(credential) = credential {
                request = request.insert_header(("authorization", credential));
            }
            // A client-asserted identity must never be honoured.
            request = request.insert_header(("x-user-id", "00000000-0000-0000-0000-0000000000ff"));

            let response = test::call_service(&app, request.to_request()).await;

            assert_eq!(
                response.status(),
                StatusCode::UNAUTHORIZED,
                "credential {credential:?} should not authenticate"
            );
        }
    }

    // -- Request id --

    #[actix_web::test]
    async fn a_supplied_request_id_is_accepted() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/rid/supplied", web::get().to(identity_probe)),
        )
        .await;

        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/rid/supplied")
                .insert_header(("x-request-id", "corr-1234"))
                .to_request(),
        )
        .await;

        assert_eq!(response.status(), StatusCode::OK);
        assert!(
            response.headers().get("x-request-id").is_none(),
            "the correlation id is logged but never echoed to the caller"
        );
    }

    #[actix_web::test]
    async fn a_missing_request_id_does_not_fail_the_request() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/rid/absent", web::get().to(identity_probe)),
        )
        .await;

        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/rid/absent")
                .to_request(),
        )
        .await;

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[actix_web::test]
    async fn a_non_ascii_request_id_falls_back_instead_of_erroring() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/rid/binary", web::get().to(identity_probe)),
        )
        .await;

        // A header whose bytes are not valid UTF-8 takes the `to_str().ok()`
        // failure path inside the middleware.
        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/rid/binary")
                .insert_header((
                    HeaderName::from_static("x-request-id"),
                    HeaderValue::from_bytes(&[0xC3, 0x28]).expect("opaque header bytes"),
                ))
                .to_request(),
        )
        .await;

        assert_eq!(response.status(), StatusCode::OK);
    }

    #[actix_web::test]
    async fn an_empty_request_id_is_accepted() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/rid/empty", web::get().to(identity_probe)),
        )
        .await;

        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/rid/empty")
                .insert_header(("x-request-id", ""))
                .to_request(),
        )
        .await;

        assert_eq!(response.status(), StatusCode::OK);
    }

    // -- Metrics --
    //
    // Prometheus registries are process-global, so each case uses a route
    // pattern nothing else touches. That keeps the label series private to the
    // case and the assertions independent of test order.

    #[actix_web::test]
    async fn a_successful_request_is_counted_once_per_call() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/metrics/counted", web::get().to(identity_probe)),
        )
        .await;

        for expected in 1..=3u64 {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri("/wp02/metrics/counted")
                    .to_request(),
            )
            .await;
            assert_eq!(response.status(), StatusCode::OK);

            assert_eq!(counter("GET", "/wp02/metrics/counted", "200"), expected);
            assert_eq!(duration_samples("GET", "/wp02/metrics/counted"), expected);
        }
    }

    #[actix_web::test]
    async fn a_failing_handler_is_counted_under_its_error_status() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/metrics/failing", web::get().to(failing_probe)),
        )
        .await;

        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/metrics/failing")
                .to_request(),
        )
        .await;

        assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(counter("GET", "/wp02/metrics/failing", "500"), 1);
        assert_eq!(
            counter("GET", "/wp02/metrics/failing", "200"),
            0,
            "a failure must not be counted as a success"
        );
    }

    #[actix_web::test]
    async fn the_method_is_part_of_the_metric_label_set() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/metrics/method", web::get().to(identity_probe))
                .route("/wp02/metrics/method", web::post().to(identity_probe)),
        )
        .await;

        test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/metrics/method")
                .to_request(),
        )
        .await;

        assert_eq!(counter("GET", "/wp02/metrics/method", "200"), 1);
        assert_eq!(
            counter("POST", "/wp02/metrics/method", "200"),
            0,
            "the POST series must be untouched"
        );
    }

    /// An unrouted request has no match pattern, so it is bucketed under a
    /// single `unmatched` label rather than exploding cardinality with one
    /// series per bad URL.
    #[actix_web::test]
    async fn unrouted_requests_share_one_label() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/metrics/routed", web::get().to(identity_probe)),
        )
        .await;

        let before = counter("GET", "unmatched", "404");

        for uri in ["/wp02/no-such-route", "/wp02/also-missing"] {
            let response =
                test::call_service(&app, test::TestRequest::get().uri(uri).to_request()).await;
            assert_eq!(response.status(), StatusCode::NOT_FOUND);
        }

        assert_eq!(
            counter("GET", "unmatched", "404"),
            before + 2,
            "both misses land in the same series"
        );
    }

    #[actix_web::test]
    async fn rendered_metrics_expose_the_request_counters() {
        let app = test::init_service(
            App::new()
                .wrap(RequestId)
                .route("/wp02/metrics/rendered", web::get().to(identity_probe)),
        )
        .await;

        test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/wp02/metrics/rendered")
                .to_request(),
        )
        .await;

        let rendered = render_metrics();

        assert!(rendered.contains("http_requests_total"), "{rendered}");
        assert!(
            rendered.contains("http_request_duration_seconds_bucket"),
            "the histogram must be exported with its buckets"
        );
        assert!(
            rendered.contains("/wp02/metrics/rendered"),
            "the route label must reach the exposition format"
        );
        assert!(
            rendered.contains("# TYPE http_requests_total counter"),
            "output must be valid Prometheus text format"
        );
    }

    #[actix_web::test]
    async fn rendering_metrics_twice_is_side_effect_free() {
        let first = render_metrics();
        let second = render_metrics();

        // Only the histogram sums move, and only when requests are served in
        // between; with no traffic the two renderings agree line for line on
        // the metric names they export.
        let names = |rendered: &str| {
            let mut lines: Vec<String> = rendered
                .lines()
                .filter(|line| line.starts_with("# TYPE"))
                .map(str::to_string)
                .collect();
            lines.sort();
            lines
        };

        assert_eq!(names(&first), names(&second));
    }
}
