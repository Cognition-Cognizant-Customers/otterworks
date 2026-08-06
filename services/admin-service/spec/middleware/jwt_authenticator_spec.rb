require 'rails_helper'

# Unit coverage for the Rack middleware itself: path exclusions, header parsing
# and the env keys downstream controllers rely on.
RSpec.describe JwtAuthenticator do
  let(:downstream_calls) { [] }
  let(:downstream) do
    calls = downstream_calls
    lambda do |env|
      calls << env
      [200, { 'Content-Type' => 'application/json' }, ['{}']]
    end
  end
  let(:middleware) { described_class.new(downstream) }

  def env_for(path, headers = {})
    Rack::MockRequest.env_for(path).merge(headers)
  end

  def call(path, headers = {})
    middleware.call(env_for(path, headers))
  end

  describe 'excluded paths' do
    described_class::EXCLUDED_PATHS.each do |path|
      it "passes #{path} through without a token" do
        status, = call(path)

        expect(status).to eq(200)
        expect(downstream_calls.size).to eq(1)
      end
    end

    it 'matches excluded paths exactly, not by prefix' do
      status, _headers, body = call('/health/deep')

      expect(status).to eq(401)
      expect(JSON.parse(body.first)['error']).to eq('Missing authorization token')
      expect(downstream_calls).to be_empty
    end

    it 'does not exclude a sub-path of the chaos endpoint' do
      status, = call('/api/v1/admin/chaos/reset')

      expect(status).to eq(401)
    end

    it 'is case sensitive about excluded paths' do
      status, = call('/HEALTH')

      expect(status).to eq(401)
    end
  end

  describe 'protected paths' do
    let(:path) { '/api/v1/admin/users' }

    it 'returns a JSON 401 when the Authorization header is absent' do
      status, headers, body = call(path)

      expect(status).to eq(401)
      expect(headers['Content-Type']).to eq('application/json')
      expect(JSON.parse(body.first)).to eq('error' => 'Missing authorization token')
    end

    it 'returns "Invalid or expired token" when the signature does not verify' do
      forged = JWT.encode({ sub: SecureRandom.uuid }, 'wrong-secret', 'HS256')
      status, _headers, body = call(path, 'HTTP_AUTHORIZATION' => "Bearer #{forged}")

      expect(status).to eq(401)
      expect(JSON.parse(body.first)['error']).to eq('Invalid or expired token')
      expect(downstream_calls).to be_empty
    end

    it 'rejects an expired token' do
      expired = JWT.encode({ sub: SecureRandom.uuid, exp: 1.hour.ago.to_i },
                           Rails.application.secrets.jwt_secret, 'HS256')
      status, _headers, body = call(path, 'HTTP_AUTHORIZATION' => "Bearer #{expired}")

      expect(status).to eq(401)
      expect(JSON.parse(body.first)['error']).to eq('Invalid or expired token')
    end

    it 'populates the jwt.* env keys for a valid token' do
      user_id = SecureRandom.uuid
      token = jwt_token(user_id: user_id, email: 'someone@otterworks.com', role: 'admin')
      status, = call(path, 'HTTP_AUTHORIZATION' => "Bearer #{token}")

      expect(status).to eq(200)
      env = downstream_calls.first
      expect(env['jwt.user_id']).to eq(user_id)
      expect(env['jwt.user_email']).to eq('someone@otterworks.com')
      expect(env['jwt.user_role']).to eq('admin')
      expect(env['jwt.payload']).to include('sub' => user_id)
    end

    it 'passes a token through regardless of the role it claims' do
      token = jwt_token(role: 'viewer')
      status, = call(path, 'HTTP_AUTHORIZATION' => "Bearer #{token}")

      expect(status).to eq(200)
      expect(downstream_calls.first['jwt.user_role']).to eq('viewer')
    end

    it 'takes the last whitespace-separated segment as the token' do
      token = jwt_token
      status, = call(path, 'HTTP_AUTHORIZATION' => "Bearer   #{token}")

      expect(status).to eq(200)
    end

    it 'rejects an empty Authorization header' do
      status, = call(path, 'HTTP_AUTHORIZATION' => '')

      expect(status).to eq(401)
    end
  end
end
