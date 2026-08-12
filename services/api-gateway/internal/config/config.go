package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config holds all configuration for the API Gateway.
type Config struct {
	Port     string
	LogLevel string

	// Backend service URLs
	AuthServiceURL         string
	FileServiceURL         string
	DocumentServiceURL     string
	CollabServiceURL       string
	NotificationServiceURL string
	SearchServiceURL       string
	AnalyticsServiceURL    string
	AdminServiceURL        string
	AuditServiceURL        string
	ReportServiceURL       string

	// Rate limiting
	RateLimitRPS int

	// JWT
	JWTSecret string

	// CORS
	CORSAllowedOrigins []string
	CORSAllowedMethods []string
	CORSAllowedHeaders []string
	CORSMaxAge         int

	// Graceful shutdown
	ShutdownTimeout time.Duration

	// Circuit breaker
	CBMaxRequests  uint32
	CBInterval     time.Duration
	CBTimeout      time.Duration
	CBFailureRatio float64
}

// Validate checks that required security-sensitive configuration is present.
func (c *Config) Validate() error {
	if c.JWTSecret == "" {
		return fmt.Errorf("JWT_SECRET environment variable is required but not set")
	}
	return nil
}

// Load reads configuration from environment variables with sensible defaults.
func Load() *Config {
	return &Config{
		Port:     getEnv("PORT", "8080"),
		LogLevel: getEnv("LOG_LEVEL", "info"),

		AuthServiceURL:         getEnv("AUTH_SERVICE_URL", "http://auth-service:8081"),
		FileServiceURL:         getEnv("FILE_SERVICE_URL", "http://file-service:8082"),
		DocumentServiceURL:     getEnv("DOCUMENT_SERVICE_URL", "http://document-service:8083"),
		CollabServiceURL:       getEnv("COLLAB_SERVICE_URL", "http://collab-service:8084"),
		NotificationServiceURL: getEnv("NOTIFICATION_SERVICE_URL", "http://notification-service:8086"),
		SearchServiceURL:       getEnv("SEARCH_SERVICE_URL", "http://search-service:8087"),
		AnalyticsServiceURL:    getEnv("ANALYTICS_SERVICE_URL", "http://analytics-service:8088"),
		AdminServiceURL:        getEnv("ADMIN_SERVICE_URL", "http://admin-service:8089"),
		AuditServiceURL:        getEnv("AUDIT_SERVICE_URL", "http://audit-service:8090"),
		ReportServiceURL:       getEnv("REPORT_SERVICE_URL", "http://report-service:8091"),

		RateLimitRPS: getEnvInt("RATE_LIMIT_RPS", 100),

		JWTSecret: getEnv("JWT_SECRET", ""),

		CORSAllowedOrigins: getEnvSlice("CORS_ALLOWED_ORIGINS", []string{"http://localhost:3000", "http://localhost:4200", "https://localhost", "capacitor://localhost"}),
		CORSAllowedMethods: getEnvSlice("CORS_ALLOWED_METHODS", []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}),
		CORSAllowedHeaders: getEnvSlice("CORS_ALLOWED_HEADERS", []string{"Accept", "Authorization", "Content-Type", "X-Request-ID"}),
		CORSMaxAge:         getEnvInt("CORS_MAX_AGE", 300),

		ShutdownTimeout: time.Duration(getEnvInt("SHUTDOWN_TIMEOUT_SECONDS", 30)) * time.Second,

		CBMaxRequests:  uint32(getEnvInt("CB_MAX_REQUESTS", 5)),
		CBInterval:     time.Duration(getEnvInt("CB_INTERVAL_SECONDS", 60)) * time.Second,
		CBTimeout:      time.Duration(getEnvInt("CB_TIMEOUT_SECONDS", 30)) * time.Second,
		CBFailureRatio: getEnvFloat("CB_FAILURE_RATIO", 0.6),
	}
}

// ServiceRoutes returns a map of route prefix to backend service URL.
func (c *Config) ServiceRoutes() map[string]string {
	return map[string]string{
		"/api/v1/auth":          c.AuthServiceURL,
		"/api/v1/files":         c.FileServiceURL,
		"/api/v1/folders":       c.FileServiceURL,
		"/api/v1/documents":     c.DocumentServiceURL,
		"/api/v1/templates":     c.DocumentServiceURL,
		"/api/v1/collab":        c.CollabServiceURL,
		"/socket.io":            c.CollabServiceURL,
		"/api/v1/notifications": c.NotificationServiceURL,
		"/api/v1/preferences":   c.NotificationServiceURL,
		"/api/v1/search":        c.SearchServiceURL,
		"/api/v1/analytics":     c.AnalyticsServiceURL,
		"/api/v1/admin":         c.AdminServiceURL,
		"/api/v1/audit":         c.AuditServiceURL,
		"/api/v1/reports":       c.ReportServiceURL,
		"/api/v1/settings":      c.AuthServiceURL,
	}
}

func getEnv(key, fallback string) string {
	if val, ok := os.LookupEnv(key); ok {
		return val
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	if val, ok := os.LookupEnv(key); ok {
		if i, err := strconv.Atoi(val); err == nil {
			return i
		}
	}
	return fallback
}

func getEnvFloat(key string, fallback float64) float64 {
	if val, ok := os.LookupEnv(key); ok {
		if f, err := strconv.ParseFloat(val, 64); err == nil {
			return f
		}
	}
	return fallback
}

func getEnvSlice(key string, fallback []string) []string {
	if val, ok := os.LookupEnv(key); ok && val != "" {
		return strings.Split(val, ",")
	}
	return fallback
}
