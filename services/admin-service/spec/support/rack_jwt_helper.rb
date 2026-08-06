# Helpers for request specs, which — unlike controller specs — run through the
# full Rack stack and therefore through JwtAuthenticator and Rack::Attack.
module RackJwtHelper
  # Mirrors JwtAuthenticator#decode_token's secret resolution so request specs
  # are independent of whether JWT_SECRET happens to be set in the environment.
  def middleware_jwt_secret
    Rails.application.credentials.jwt_secret ||
      ENV.fetch('JWT_SECRET', Rails.application.secrets.jwt_secret)
  end

  def encoded_jwt(payload, algorithm: 'HS256', secret: middleware_jwt_secret)
    JWT.encode(payload, secret, algorithm)
  end

  def valid_jwt_payload(role: 'super_admin', user_id: '11111111-1111-1111-1111-111111111111')
    {
      sub: user_id,
      email: 'admin@otterworks.com',
      role: role,
      exp: 1.hour.from_now.to_i,
      iat: Time.current.to_i
    }
  end

  def bearer(token)
    { 'Authorization' => "Bearer #{token}" }
  end

  # A token the middleware accepts, for pinning what happens *after* authentication.
  def valid_bearer(role: 'super_admin')
    bearer(encoded_jwt(valid_jwt_payload(role: role)))
  end
end

RSpec.configure do |config|
  config.include RackJwtHelper, type: :request

  # Rack::Attack is mounted unconditionally (config/application.rb) and counts
  # through Rails.cache, which is a :null_store in the test environment. Give it
  # a real store, isolated per example, so throttling neither errors nor leaks
  # counters between examples.
  config.before(type: :request) do
    Rack::Attack.cache.store = ActiveSupport::Cache::MemoryStore.new
  end
end
