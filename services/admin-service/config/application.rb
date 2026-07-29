require_relative 'boot'

require 'rails'
require 'active_model/railtie'
require 'active_job/railtie'
require 'active_record/railtie'
require 'action_controller/railtie'
require 'action_mailer/railtie'
require 'rails/test_unit/railtie'

Bundler.require(*Rails.groups)

require_relative '../app/middleware/jwt_authenticator'

module AdminService
  class Application < Rails::Application
    config.load_defaults 7.2
    config.api_only = true

    # Exclude middleware from Zeitwerk autoloading — it is manually
    # required via require_relative above, and Zeitwerk's inflection
    # rules would expect JWTAuthenticator instead of JwtAuthenticator.
    config.autoload_paths -= [root.join('app/middleware').to_s]
    config.eager_load_paths -= [root.join('app/middleware').to_s]
    config.active_job.queue_adapter = :sidekiq
    config.log_formatter = ::Logger::Formatter.new
    config.time_zone = 'UTC'

    config.middleware.use Rack::Attack
    config.middleware.use JwtAuthenticator

    # Structured JSON logging
    config.lograge.enabled = true
    config.lograge.formatter = Lograge::Formatters::Json.new
    config.lograge.custom_payload do |_controller|
      {
        service: 'admin-service',
        host: Socket.gethostname
      }
    end
  end
end
