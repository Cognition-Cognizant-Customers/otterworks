module JwtHelper
  DEFAULT_EMAIL = 'admin@otterworks.com'.freeze
  DEFAULT_ROLE = 'super_admin'.freeze

  def jwt_token(user_id: SecureRandom.uuid, email: DEFAULT_EMAIL, role: DEFAULT_ROLE)
    payload = {
      sub: user_id,
      email: email,
      role: role,
      exp: 24.hours.from_now.to_i,
      iat: Time.current.to_i
    }
    secret = Rails.application.secrets.jwt_secret
    JWT.encode(payload, secret, 'HS256')
  end

  def auth_headers(user_id: SecureRandom.uuid, email: DEFAULT_EMAIL, role: DEFAULT_ROLE)
    token = jwt_token(user_id: user_id, email: email, role: role)
    { 'Authorization' => "Bearer #{token}" }
  end

  def set_jwt_env(request, user_id: SecureRandom.uuid, email: DEFAULT_EMAIL, role: DEFAULT_ROLE)
    request.env['jwt.user_id'] = user_id
    request.env['jwt.user_email'] = email
    request.env['jwt.user_role'] = role
  end
end

RSpec.configure do |config|
  config.include JwtHelper
end
