require 'rails_helper'

RSpec.describe JwtAuthenticator do
  let(:app) { ->(env) { [200, { 'Content-Type' => 'application/json' }, [{ ok: true, env: env.slice('jwt.user_id', 'jwt.user_email', 'jwt.user_role') }.to_json]] } }
  let(:middleware) { described_class.new(app) }
  let(:secret) { 'test-jwt-secret' }

  around do |example|
    original = ENV['JWT_SECRET']
    ENV['JWT_SECRET'] = secret
    example.run
    ENV['JWT_SECRET'] = original
  end

  def make_request(path, headers = {})
    env = Rack::MockRequest.env_for(path)
    headers.each { |k, v| env["HTTP_#{k.upcase.tr('-', '_')}"] = v }
    middleware.call(env)
  end

  def encode_token(payload, key: secret, algorithm: 'HS256')
    JWT.encode(payload, key, algorithm)
  end

  def valid_payload(overrides = {})
    {
      sub: 'user-123',
      email: 'admin@otterworks.com',
      role: 'super_admin',
      exp: 1.hour.from_now.to_i,
      iat: Time.current.to_i
    }.merge(overrides)
  end

  describe 'excluded paths' do
    JwtAuthenticator::EXCLUDED_PATHS.each do |path|
      it "skips authentication for #{path}" do
        status, = make_request(path)
        expect(status).to eq(200)
      end
    end

    it 'does not skip authentication for non-excluded paths' do
      status, = make_request('/api/v1/admin/features')
      expect(status).to eq(401)
    end
  end

  describe 'token extraction' do
    it 'returns 401 with a JSON error when the Authorization header is missing' do
      status, headers, body = make_request('/api/v1/admin/features')
      expect(status).to eq(401)
      expect(headers['Content-Type']).to eq('application/json')
      expect(JSON.parse(body.first)['error']).to eq('Missing authorization token')
    end

    it 'returns 401 when the Authorization header is not a Bearer token' do
      status, _, body = make_request('/api/v1/admin/features', 'Authorization' => 'Basic abc123')
      expect(status).to eq(401)
      expect(JSON.parse(body.first)['error']).to eq('Missing authorization token')
    end
  end

  describe 'token validation' do
    it 'returns 401 for a token signed with the wrong secret' do
      token = encode_token(valid_payload, key: 'wrong-secret')
      status, _, body = make_request('/api/v1/admin/features', 'Authorization' => "Bearer #{token}")
      expect(status).to eq(401)
      expect(JSON.parse(body.first)['error']).to eq('Invalid or expired token')
    end

    it 'returns 401 for an expired token' do
      token = encode_token(valid_payload(exp: 1.hour.ago.to_i))
      status, = make_request('/api/v1/admin/features', 'Authorization' => "Bearer #{token}")
      expect(status).to eq(401)
    end

    it 'returns 401 for a malformed token' do
      status, = make_request('/api/v1/admin/features', 'Authorization' => 'Bearer not-a-jwt')
      expect(status).to eq(401)
    end

    it 'accepts an HS384-signed token' do
      token = encode_token(valid_payload, algorithm: 'HS384')
      status, = make_request('/api/v1/admin/features', 'Authorization' => "Bearer #{token}")
      expect(status).to eq(200)
    end
  end

  describe 'successful authentication' do
    it 'passes the request through and populates jwt env keys' do
      token = encode_token(valid_payload)
      status, _, body = make_request('/api/v1/admin/features', 'Authorization' => "Bearer #{token}")
      expect(status).to eq(200)

      parsed = JSON.parse(body.first)['env']
      expect(parsed['jwt.user_id']).to eq('user-123')
      expect(parsed['jwt.user_email']).to eq('admin@otterworks.com')
      expect(parsed['jwt.user_role']).to eq('super_admin')
    end
  end

  describe 'secret resolution' do
    it 'falls back to ENV["JWT_SECRET"] when credentials have no jwt_secret' do
      allow(Rails.application.credentials).to receive(:jwt_secret).and_return(nil)
      token = encode_token(valid_payload)
      status, = make_request('/api/v1/admin/features', 'Authorization' => "Bearer #{token}")
      expect(status).to eq(200)
    end

    it 'prefers the credentials jwt_secret over the environment variable' do
      allow(Rails.application.credentials).to receive(:jwt_secret).and_return('credentials-secret')
      token = encode_token(valid_payload, key: 'credentials-secret')
      status, = make_request('/api/v1/admin/features', 'Authorization' => "Bearer #{token}")
      expect(status).to eq(200)
    end
  end
end
