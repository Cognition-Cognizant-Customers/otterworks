require 'rails_helper'

# Authorization negatives for the admin API (WP-10).
#
# Controller specs bypass the Rack stack, so until now nothing exercised
# `JwtAuthenticator` — the only thing standing between the internet and every
# admin route. These are request specs, so the middleware really runs.
#
# FINDING (pinned, not fixed): authentication is all the service has. No route
# checks `jwt.user_role`, so any *valid* token — viewer, unknown role, or no
# role at all — is granted full admin access. The examples tagged
# "currently permits" document that gap; they will fail deliberately when
# role-based authorization is added.

# Every route that sits behind JwtAuthenticator (config/routes.rb).
PROTECTED_ADMIN_ROUTES = [
  [:get,    '/api/v1/admin/users'],
  [:get,    '/api/v1/admin/users/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/users/11111111-1111-1111-1111-111111111111'],
  [:delete, '/api/v1/admin/users/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/users/11111111-1111-1111-1111-111111111111/suspend'],
  [:put,    '/api/v1/admin/users/11111111-1111-1111-1111-111111111111/activate'],
  [:get,    '/api/v1/admin/health/services'],
  [:get,    '/api/v1/admin/features'],
  [:post,   '/api/v1/admin/features'],
  [:get,    '/api/v1/admin/features/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/features/11111111-1111-1111-1111-111111111111'],
  [:delete, '/api/v1/admin/features/11111111-1111-1111-1111-111111111111'],
  [:get,    '/api/v1/admin/config'],
  [:get,    '/api/v1/admin/config/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/config/11111111-1111-1111-1111-111111111111'],
  [:get,    '/api/v1/admin/audit-logs'],
  [:get,    '/api/v1/admin/audit-logs/11111111-1111-1111-1111-111111111111'],
  [:get,    '/api/v1/admin/quotas/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/quotas/11111111-1111-1111-1111-111111111111'],
  [:get,    '/api/v1/admin/metrics/summary'],
  [:get,    '/api/v1/admin/announcements'],
  [:post,   '/api/v1/admin/announcements'],
  [:get,    '/api/v1/admin/announcements/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/announcements/11111111-1111-1111-1111-111111111111'],
  [:delete, '/api/v1/admin/announcements/11111111-1111-1111-1111-111111111111'],
  [:get,    '/api/v1/admin/incidents'],
  [:post,   '/api/v1/admin/incidents'],
  [:get,    '/api/v1/admin/incidents/11111111-1111-1111-1111-111111111111'],
  [:put,    '/api/v1/admin/incidents/11111111-1111-1111-1111-111111111111'],
  [:delete, '/api/v1/admin/incidents/11111111-1111-1111-1111-111111111111'],
  [:post,   '/api/v1/admin/incidents/11111111-1111-1111-1111-111111111111/trigger_session'],
  [:post,   '/api/v1/admin/bulk/users'],
  [:get,    '/api/v1/admin/settings/auto_investigate'],
  [:put,    '/api/v1/admin/settings/auto_investigate']
].freeze

RSpec.describe 'Admin API authorization', type: :request do
  describe 'no credentials' do
    PROTECTED_ADMIN_ROUTES.each do |verb, path|
      it "rejects #{verb.to_s.upcase} #{path} with 401" do
        public_send(verb, path)
        expect(response).to have_http_status(:unauthorized)
      end
    end

    it 'explains that the token is missing' do
      get '/api/v1/admin/users'
      expect(response.parsed_body['error']).to eq('Missing authorization token')
    end

    it 'answers as JSON' do
      get '/api/v1/admin/users'
      expect(response.media_type).to eq('application/json')
    end
  end

  describe 'malformed Authorization headers' do
    {
      'an empty header' => '',
      'a bare token with no scheme' => 'abcdef',
      'the wrong scheme' => 'Token abcdef',
      'Basic auth' => 'Basic YWRtaW46YWRtaW4=',
      'a lowercase bearer scheme' => 'bearer abcdef',
      'the scheme with no token' => 'Bearer'
    }.each do |description, header|
      it "rejects #{description}" do
        get '/api/v1/admin/users', headers: { 'Authorization' => header }
        expect(response).to have_http_status(:unauthorized)
      end
    end

    it 'reports a missing token when the scheme is unrecognised' do
      get '/api/v1/admin/users', headers: { 'Authorization' => 'Token abcdef' }
      expect(response.parsed_body['error']).to eq('Missing authorization token')
    end

    it 'reports an invalid token when the scheme is right but the token is not a JWT' do
      get '/api/v1/admin/users', headers: bearer('not.a.jwt')
      expect(response.parsed_body['error']).to eq('Invalid or expired token')
    end
  end

  describe 'invalid tokens' do
    it 'rejects a token signed with the wrong secret' do
      token = encoded_jwt(valid_jwt_payload, secret: 'definitely-not-the-secret')
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects an unsigned alg:none token' do
      token = JWT.encode(valid_jwt_payload, nil, 'none')
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects an algorithm outside the allow-list (HS512)' do
      token = encoded_jwt(valid_jwt_payload, algorithm: 'HS512')
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'accepts HS384, which is on the allow-list alongside HS256' do
      token = encoded_jwt(valid_jwt_payload, algorithm: 'HS384')
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:ok)
    end

    it 'rejects a token that is not yet valid (nbf in the future)' do
      token = encoded_jwt(valid_jwt_payload.merge(nbf: 1.hour.from_now.to_i))
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects a token whose payload has been tampered with' do
      token = encoded_jwt(valid_jwt_payload)
      header, _payload, signature = token.split('.')
      forged = Base64.urlsafe_encode64(valid_jwt_payload.merge(role: 'super_admin', sub: 'attacker').to_json,
                                       padding: false)
      get '/api/v1/admin/users', headers: bearer([header, forged, signature].join('.'))
      expect(response).to have_http_status(:unauthorized)
    end
  end

  describe 'token expiry boundary' do
    let(:now) { Time.utc(2026, 1, 1, 12, 0, 0) }

    around do |example|
      travel_to(now) { example.run }
    end

    it 'rejects a token that expired one second ago' do
      get '/api/v1/admin/users', headers: bearer(encoded_jwt(valid_jwt_payload.merge(exp: now.to_i - 1)))
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects a token expiring exactly now' do
      get '/api/v1/admin/users', headers: bearer(encoded_jwt(valid_jwt_payload.merge(exp: now.to_i)))
      expect(response).to have_http_status(:unauthorized)
    end

    it 'accepts a token expiring one second from now' do
      get '/api/v1/admin/users', headers: bearer(encoded_jwt(valid_jwt_payload.merge(exp: now.to_i + 1)))
      expect(response).to have_http_status(:ok)
    end
  end

  describe 'authenticated but not authorized (no RBAC exists yet)' do
    let!(:target) { create(:admin_user) }

    %w[viewer editor admin].each do |role|
      it "currently permits a '#{role}' token to list every admin user" do
        get '/api/v1/admin/users', headers: valid_bearer(role: role)
        expect(response).to have_http_status(:ok)
      end
    end

    it 'currently permits a viewer token to delete a user' do
      delete "/api/v1/admin/users/#{target.id}", headers: valid_bearer(role: 'viewer')
      expect(response).to have_http_status(:no_content)
      expect(target.reload.status).to eq('deleted')
    end

    it 'currently permits a viewer token to run a bulk suspension' do
      post '/api/v1/admin/bulk/users',
           params: { operation: 'suspend', user_ids: [target.id] },
           headers: valid_bearer(role: 'viewer')
      expect(response).to have_http_status(:ok)
    end

    it 'currently permits a viewer token to raise a user to super_admin' do
      put "/api/v1/admin/users/#{target.id}",
          params: { user: { role: 'super_admin' } },
          headers: valid_bearer(role: 'viewer')
      expect(target.reload.role).to eq('super_admin')
    end

    it 'currently permits a token carrying an unknown role' do
      get '/api/v1/admin/users', headers: valid_bearer(role: 'definitely_not_a_role')
      expect(response).to have_http_status(:ok)
    end

    it 'currently permits a token carrying no role claim at all' do
      token = encoded_jwt(valid_jwt_payload.except(:role))
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:ok)
    end

    it 'currently permits a token carrying no subject claim' do
      token = encoded_jwt(valid_jwt_payload.except(:sub))
      get '/api/v1/admin/users', headers: bearer(token)
      expect(response).to have_http_status(:ok)
    end

    it 'attributes the audit trail to the token subject' do
      delete "/api/v1/admin/users/#{target.id}", headers: valid_bearer(role: 'viewer')
      expect(AuditLog.by_action('user.deleted').last.actor_id).to eq(valid_jwt_payload[:sub])
    end

    it 'ignores a client-supplied X-User-ID header when attributing the audit trail' do
      delete "/api/v1/admin/users/#{target.id}",
             headers: valid_bearer(role: 'viewer').merge('X-User-ID' => '99999999-9999-9999-9999-999999999999')
      expect(AuditLog.by_action('user.deleted').last.actor_id).to eq(valid_jwt_payload[:sub])
    end
  end

  describe 'unauthenticated routes are unauthenticated by design' do
    it 'serves /health without a token' do
      get '/health'
      expect(response).to have_http_status(:ok)
    end

    it 'serves /metrics without a token' do
      get '/metrics'
      expect(response).to have_http_status(:ok)
    end

    it 'lets the Grafana alert webhook through without a JWT' do
      # Not 401: the webhook authenticates with ALERT_WEBHOOK_SECRET instead,
      # and that secret is unset in test, so it reaches the controller.
      allow(ENV).to receive(:fetch).and_call_original
      allow(ENV).to receive(:fetch).with('ALERT_WEBHOOK_SECRET', nil).and_return(nil)
      post '/api/v1/admin/alerts/ingest', params: { alerts: 'nope' }
      expect(response).to have_http_status(:bad_request)
    end

    it 'rejects the alert webhook when the shared secret does not match' do
      allow(ENV).to receive(:fetch).and_call_original
      allow(ENV).to receive(:fetch).with('ALERT_WEBHOOK_SECRET', nil).and_return('expected-secret')
      post '/api/v1/admin/alerts/ingest',
           params: { alerts: [] },
           headers: { 'X-Alert-Secret' => 'wrong-secret' }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'accepts the alert webhook when the shared secret matches' do
      allow(ENV).to receive(:fetch).and_call_original
      allow(ENV).to receive(:fetch).with('ALERT_WEBHOOK_SECRET', nil).and_return('expected-secret')
      post '/api/v1/admin/alerts/ingest',
           params: { alerts: [] }, as: :json,
           headers: { 'X-Alert-Secret' => 'expected-secret' }
      expect(response).to have_http_status(:ok)
    end

    it 'accepts the alert secret presented as a bearer token' do
      allow(ENV).to receive(:fetch).and_call_original
      allow(ENV).to receive(:fetch).with('ALERT_WEBHOOK_SECRET', nil).and_return('expected-secret')
      post '/api/v1/admin/alerts/ingest',
           params: { alerts: [] }, as: :json,
           headers: bearer('expected-secret')
      expect(response).to have_http_status(:ok)
    end

    it 'currently answers 500, not 400, when an alert entry is not an object' do
      # FINDING (pinned, not fixed): AlertsController#process_alert calls
      # `alert[:status]` without checking the element type, so a scalar entry
      # raises TypeError and ApplicationController turns it into a 500. An
      # unauthenticated caller can therefore trigger a 500 at will. Judged
      # genuine (this file is not the planted bug named in AGENTS.md).
      allow(ENV).to receive(:fetch).and_call_original
      allow(ENV).to receive(:fetch).with('ALERT_WEBHOOK_SECRET', nil).and_return(nil)
      post '/api/v1/admin/alerts/ingest', params: { alerts: ['not-an-object'] }, as: :json
      expect(response).to have_http_status(:internal_server_error)
    end

    it 'exposes only the four documented unauthenticated paths' do
      expect(JwtAuthenticator::EXCLUDED_PATHS)
        .to contain_exactly('/health', '/metrics', '/api/v1/admin/alerts/ingest', '/api/v1/admin/chaos')
    end

    it 'matches excluded paths exactly, not by prefix' do
      get '/health/services'
      expect(response).to have_http_status(:unauthorized)
    end
  end
end
