package main

import (
	"context"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/rs/zerolog"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/config"
	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/health"
	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/middleware"
	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/proxy"
)

func main() {
	cfg := config.Load()
	if err := cfg.Validate(); err != nil {
		l := zerolog.New(os.Stdout).With().Timestamp().Logger()
		l.Fatal().Err(err).Msg("invalid configuration")
	}

	// Structured JSON logging
	zerolog.TimeFieldFormat = zerolog.TimeFormatUnix
	logger := zerolog.New(os.Stdout).With().
		Timestamp().
		Str("service", "api-gateway").
		Logger()

	middleware.SetLogLevel(cfg.LogLevel)

	// OpenTelemetry tracing
	shutdownTracer := initTracer()

	// Circuit breaker manager
	cbManager := proxy.NewCircuitBreakerManager(proxy.CircuitBreakerConfig{
		MaxRequests:  cfg.CBMaxRequests,
		Interval:     cfg.CBInterval,
		Timeout:      cfg.CBTimeout,
		FailureRatio: cfg.CBFailureRatio,
	})

	// Build routes
	var routes []proxy.Route
	for prefix, target := range cfg.ServiceRoutes() {
		routes = append(routes, proxy.Route{
			Prefix:    prefix,
			TargetURL: target,
		})
	}

	// Create main router
	r := chi.NewRouter()

	// Global middleware stack
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP(cfg.TrustedProxies))
	r.Use(middleware.SecurityHeaders(middleware.DefaultSecurityHeadersConfig()))
	r.Use(middleware.Metrics)
	r.Use(middleware.Logger(logger))
	r.Use(chimw.Recoverer)
	r.Use(chimw.Compress(5))

	// Rate limiting
	rateLimiter := middleware.NewRateLimiter(cfg.RateLimitRPS)
	r.Use(rateLimiter.Handler)

	// CORS
	r.Use(middleware.CORS(middleware.CORSConfig{
		AllowedOrigins:   cfg.CORSAllowedOrigins,
		AllowedMethods:   cfg.CORSAllowedMethods,
		AllowedHeaders:   cfg.CORSAllowedHeaders,
		ExposedHeaders:   []string{"Link", "X-Request-ID"},
		AllowCredentials: true,
		MaxAge:           cfg.CORSMaxAge,
	}))

	// JWT validation
	r.Use(middleware.JWTAuth(middleware.JWTConfig{
		Secret:              cfg.JWTSecret,
		PublicPath:          middleware.DefaultPublicPaths(),
		PrefixPath:          middleware.DefaultPrefixPaths(),
		ProtectedPrefixPath: routePrefixes(routes),
	}))

	// Health check
	r.Get("/health", health.Handler())

	// Mount reverse proxy routes
	proxyRouter := proxy.NewRouter(proxy.RouterConfig{
		Routes:        routes,
		CBManager:     cbManager,
		Logger:        logger,
		EnableTracing: true,
	})
	r.Mount("/", proxyRouter)

	// Metrics listener, kept off the public edge
	metricsSrv := startMetricsServer(cfg.MetricsPort, logger)

	// HTTP server
	srv := &http.Server{
		Addr:         ":" + cfg.Port,
		Handler:      r,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	// Start server in background
	go func() {
		logger.Info().Str("port", cfg.Port).Msg("API Gateway starting")
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Fatal().Err(err).Msg("server failed")
		}
	}()

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	logger.Info().Msg("shutting down server...")

	ctx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		logger.Fatal().Err(err).Msg("server forced to shutdown")
	}

	if err := metricsSrv.Shutdown(ctx); err != nil {
		logger.Error().Err(err).Msg("metrics server forced to shutdown")
	}

	if shutdownTracer != nil {
		if err := shutdownTracer(ctx); err != nil {
			logger.Error().Err(err).Msg("failed to shutdown tracer")
		}
	}

	logger.Info().Msg("server exited")
}

// startMetricsServer serves Prometheus metrics on a separate listener so scraping
// never depends on a route being public at the edge.
func startMetricsServer(port string, logger zerolog.Logger) *http.Server {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())

	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		logger.Info().Str("port", port).Msg("metrics listener starting")
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error().Err(err).Msg("metrics server failed")
		}
	}()
	return srv
}

func routePrefixes(routes []proxy.Route) []string {
	prefixes := make([]string, 0, len(routes))
	for _, route := range routes {
		prefixes = append(prefixes, route.Prefix)
	}
	return prefixes
}

// initTracer sets up the OpenTelemetry trace provider.
func initTracer() func(context.Context) error {
	ctx := context.Background()

	l := zerolog.New(os.Stdout).With().Timestamp().Logger()

	exporter, err := otlptracehttp.New(ctx)
	if err != nil {
		l.Warn().Err(err).Msg("failed to create OTLP exporter, tracing disabled")
		return nil
	}

	res, err := resource.New(ctx,
		resource.WithAttributes(
			semconv.ServiceName("api-gateway"),
			semconv.ServiceVersion("0.1.0"),
		),
	)
	if err != nil {
		exporter.Shutdown(ctx)
		l.Warn().Err(err).Msg("failed to create trace resource")
		return nil
	}

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
	)

	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	return tp.Shutdown
}
