Rails.application.routes.draw do
  root to: 'health#show'

  # Health check (excluded from JWT auth)
  get '/health', to: 'health#show'
  get '/metrics', to: 'health#metrics'

  # Admin API
  namespace :api do
    namespace :v1 do
      namespace :admin do
        # User Management
        resources :users, only: %i[index show update destroy] do
          member do
            put :suspend, to: 'users#suspend'
            put :activate, to: 'users#activate'
          end
        end

        # System Health
        get 'health/services', to: 'health#services'

        # Feature Flags
        resources :features, only: %i[index show create update destroy]

        # System Configuration
        resources :config, only: %i[index show update]

        # Audit Log Viewer
        resources :audit_logs, only: %i[index show], path: 'audit-logs'

        # Storage Quotas
        get 'quotas/:user_id', to: 'quotas#show', as: :quota
        put 'quotas/:user_id', to: 'quotas#update'

        # System Metrics
        get 'metrics/summary', to: 'metrics#summary'

        # Announcements
        resources :announcements, only: %i[index show create update destroy]

        # Incidents (Automated Incident Response)
        resources :incidents, only: %i[index show create update destroy] do
          member do
            post :trigger_session, to: 'incidents#trigger_session'
          end
        end

        # Bulk Operations
        post 'bulk/users', to: 'bulk#users'

        # Chaos injection (demo/workshop use — protected by X-Chaos-Secret header)
        post  'chaos', to: 'chaos#trigger'
        delete 'chaos', to: 'chaos#reset'

        # Settings
        get 'settings/auto_investigate', to: 'settings#auto_investigate'
        put 'settings/auto_investigate', to: 'settings#update_auto_investigate'

        # Grafana alert webhook (no JWT — protected by X-Alert-Secret header)
        post 'alerts/ingest', to: 'alerts#ingest'
      end
    end
  end
end
