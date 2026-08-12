package health

import (
	"net/http"

	"github.com/Cognition-Partner-Workshops/otterworks/services/api-gateway/internal/httpx"
)

const version = "0.1.0"

// Response represents the health check response payload.
type Response struct {
	Status  string `json:"status"`
	Version string `json:"version"`
}

// Handler returns an HTTP handler that responds with the service health status.
func Handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		httpx.WriteJSON(w, http.StatusOK, Response{
			Status:  "healthy",
			Version: version,
		})
	}
}
