require 'rails_helper'

# End-to-end authorization coverage: request specs run the full Rack stack, so
# JwtAuthenticator actually executes here (controller specs bypass it by stubbing
# `jwt.*` keys straight into the request env).
RSpec.describe 'Admin API authorization' do
  let(:user) { create(:admin_user) }
  let(:flag) { create(:feature_flag) }
  let(:config) { create(:system_config) }
  let(:quota) { create(:storage_quota) }
  let(:secret) { Rails.application.secrets.jwt_secret }

  def json_body
    JSON.parse(response.body)
  end

  describe 'unauthenticated callers' do
    it 'rejects GET /api/v1/admin/users' do
      get '/api/v1/admin/users'
      expect(response).to have_http_status(:unauthorized)
      expect(json_body['error']).to eq('Missing authorization token')
    end

    it 'rejects GET /api/v1/admin/users/:id' do
      get "/api/v1/admin/users/#{user.id}"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects PUT /api/v1/admin/users/:id' do
      put "/api/v1/admin/users/#{user.id}", params: { user: { display_name: 'Hacked' } }
      expect(response).to have_http_status(:unauthorized)
      expect(user.reload.display_name).not_to eq('Hacked')
    end

    it 'rejects DELETE /api/v1/admin/users/:id' do
      delete "/api/v1/admin/users/#{user.id}"
      expect(response).to have_http_status(:unauthorized)
      expect(user.reload.status).to eq('active')
    end

    it 'rejects PUT /api/v1/admin/users/:id/suspend' do
      put "/api/v1/admin/users/#{user.id}/suspend"
      expect(response).to have_http_status(:unauthorized)
      expect(user.reload.status).to eq('active')
    end

    it 'rejects PUT /api/v1/admin/users/:id/activate' do
      put "/api/v1/admin/users/#{user.id}/activate"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/health/services' do
      get '/api/v1/admin/health/services'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/features' do
      get '/api/v1/admin/features'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/features/:id' do
      get "/api/v1/admin/features/#{flag.id}"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects POST /api/v1/admin/features' do
      expect do
        post '/api/v1/admin/features', params: { feature: { name: 'sneaky_flag' } }
      end.not_to change(FeatureFlag, :count)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects PUT /api/v1/admin/features/:id' do
      put "/api/v1/admin/features/#{flag.id}", params: { feature: { enabled: true } }
      expect(response).to have_http_status(:unauthorized)
      expect(flag.reload.enabled).to be false
    end

    it 'rejects DELETE /api/v1/admin/features/:id' do
      flag
      expect { delete "/api/v1/admin/features/#{flag.id}" }.not_to change(FeatureFlag, :count)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/config' do
      get '/api/v1/admin/config'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/config/:id' do
      get "/api/v1/admin/config/#{config.id}"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects PUT /api/v1/admin/config/:id' do
      put "/api/v1/admin/config/#{config.id}", params: { config: { value: 'tampered' } }
      expect(response).to have_http_status(:unauthorized)
      expect(config.reload.value).not_to eq('tampered')
    end

    it 'rejects GET /api/v1/admin/audit-logs' do
      get '/api/v1/admin/audit-logs'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/audit-logs/:id' do
      log = create(:audit_log)
      get "/api/v1/admin/audit-logs/#{log.id}"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/quotas/:user_id' do
      get "/api/v1/admin/quotas/#{quota.user_id}"
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects PUT /api/v1/admin/quotas/:user_id' do
      put "/api/v1/admin/quotas/#{quota.user_id}", params: { quota: { tier: 'enterprise' } }
      expect(response).to have_http_status(:unauthorized)
      expect(quota.reload.tier).to eq('free')
    end

    it 'rejects GET /api/v1/admin/metrics/summary' do
      get '/api/v1/admin/metrics/summary'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/announcements' do
      get '/api/v1/admin/announcements'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects POST /api/v1/admin/announcements' do
      expect do
        post '/api/v1/admin/announcements', params: { announcement: { title: 'x', body: 'y' } }
      end.not_to change(Announcement, :count)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects PUT /api/v1/admin/announcements/:id' do
      announcement = create(:announcement)
      put "/api/v1/admin/announcements/#{announcement.id}", params: { announcement: { title: 'edited' } }
      expect(response).to have_http_status(:unauthorized)
      expect(announcement.reload.title).not_to eq('edited')
    end

    it 'rejects DELETE /api/v1/admin/announcements/:id' do
      announcement = create(:announcement)
      expect { delete "/api/v1/admin/announcements/#{announcement.id}" }.not_to change(Announcement, :count)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/incidents' do
      get '/api/v1/admin/incidents'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects POST /api/v1/admin/incidents' do
      expect do
        post '/api/v1/admin/incidents', params: { incident: { title: 'x', description: 'y' } }
      end.not_to change(Incident, :count)
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects POST /api/v1/admin/bulk/users' do
      expect do
        post '/api/v1/admin/bulk/users', params: { operation: 'delete', user_ids: [user.id] }
      end.not_to(change { user.reload.status })
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects GET /api/v1/admin/settings/auto_investigate' do
      get '/api/v1/admin/settings/auto_investigate'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects PUT /api/v1/admin/settings/auto_investigate' do
      put '/api/v1/admin/settings/auto_investigate', params: { enabled: 'false' }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'still serves the unauthenticated health endpoint' do
      get '/health'
      expect(response).to have_http_status(:ok)
    end

    it 'still serves the unauthenticated metrics endpoint' do
      get '/metrics'
      expect(response).to have_http_status(:ok)
    end
  end

  describe 'malformed or invalid credentials' do
    it 'rejects a token signed with the wrong secret' do
      token = JWT.encode({ sub: SecureRandom.uuid, role: 'super_admin' }, 'not-the-real-secret', 'HS256')
      get '/api/v1/admin/users', headers: { 'Authorization' => "Bearer #{token}" }

      expect(response).to have_http_status(:unauthorized)
      expect(json_body['error']).to eq('Invalid or expired token')
    end

    it 'rejects an expired token' do
      token = JWT.encode({ sub: SecureRandom.uuid, role: 'super_admin', exp: 1.hour.ago.to_i }, secret, 'HS256')
      get '/api/v1/admin/users', headers: { 'Authorization' => "Bearer #{token}" }

      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects an unsigned (alg=none) token' do
      token = JWT.encode({ sub: SecureRandom.uuid, role: 'super_admin' }, nil, 'none')
      get '/api/v1/admin/users', headers: { 'Authorization' => "Bearer #{token}" }

      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects a garbage token' do
      get '/api/v1/admin/users', headers: { 'Authorization' => 'Bearer not.a.jwt' }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects a Bearer prefix with no token' do
      get '/api/v1/admin/users', headers: { 'Authorization' => 'Bearer' }
      expect(response).to have_http_status(:unauthorized)
      expect(json_body['error']).to eq('Missing authorization token')
    end

    it 'rejects a non-Bearer authorization scheme' do
      get '/api/v1/admin/users', headers: { 'Authorization' => 'Basic YWRtaW46YWRtaW4=' }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'rejects a lowercase bearer prefix (scheme match is case sensitive)' do
      token = jwt_token
      get '/api/v1/admin/users', headers: { 'Authorization' => "bearer #{token}" }

      expect(response).to have_http_status(:unauthorized)
    end

    it 'accepts a valid super_admin token' do
      get '/api/v1/admin/users', headers: auth_headers
      expect(response).to have_http_status(:ok)
    end

    it 'accepts an HS384-signed token as well as HS256' do
      token = JWT.encode({ sub: SecureRandom.uuid, role: 'super_admin', exp: 1.hour.from_now.to_i },
                         secret, 'HS384')
      get '/api/v1/admin/users', headers: { 'Authorization' => "Bearer #{token}" }

      expect(response).to have_http_status(:ok)
    end

    it 'accepts a token with no expiry claim' do
      token = JWT.encode({ sub: SecureRandom.uuid, role: 'super_admin' }, secret, 'HS256')
      get '/api/v1/admin/users', headers: { 'Authorization' => "Bearer #{token}" }

      expect(response).to have_http_status(:ok)
    end
  end

  describe 'role-based authorization' do
    let(:viewer_headers) { auth_headers(role: 'viewer', email: 'viewer@otterworks.com') }
    let(:anonymous_role_headers) { auth_headers(role: nil, email: 'norole@otterworks.com') }

    it 'currently lets a viewer read the admin user list' do
      get '/api/v1/admin/users', headers: viewer_headers
      expect(response).to have_http_status(:ok)
    end

    # FINDING (documented, not fixed): admin-service performs authentication only.
    # No controller or middleware inspects `jwt.user_role`, so any caller holding a
    # valid token — whatever its role — can perform destructive admin mutations.
    it 'should refuse a viewer soft-deleting an admin user' do
      pending('admin-service has no role authorization: JwtAuthenticator authenticates but never authorizes')

      delete "/api/v1/admin/users/#{user.id}", headers: viewer_headers
      expect(response).to have_http_status(:forbidden)
    end

    it 'should refuse a viewer raising a storage quota' do
      pending('admin-service has no role authorization: JwtAuthenticator authenticates but never authorizes')

      put "/api/v1/admin/quotas/#{quota.user_id}",
          params: { quota: { tier: 'enterprise', quota_bytes: 1_099_511_627_776 } },
          headers: viewer_headers
      expect(response).to have_http_status(:forbidden)
    end

    it 'should refuse a viewer creating a feature flag' do
      pending('admin-service has no role authorization: JwtAuthenticator authenticates but never authorizes')

      post '/api/v1/admin/features', params: { feature: { name: 'viewer_made_this' } }, headers: viewer_headers
      expect(response).to have_http_status(:forbidden)
    end

    it 'should refuse a viewer running a bulk mutation' do
      pending('admin-service has no role authorization: JwtAuthenticator authenticates but never authorizes')

      post '/api/v1/admin/bulk/users', params: { operation: 'delete', user_ids: [user.id] },
                                       headers: viewer_headers
      expect(response).to have_http_status(:forbidden)
    end

    it 'should refuse a token that carries no role claim at all' do
      pending('admin-service has no role authorization: a role-less token is treated like any other')

      delete "/api/v1/admin/users/#{user.id}", headers: anonymous_role_headers
      expect(response).to have_http_status(:forbidden)
    end

    it 'records the acting viewer in the audit trail even though the action is unauthorized' do
      delete "/api/v1/admin/users/#{user.id}", headers: viewer_headers

      log = AuditLog.by_action('user.deleted').last
      expect(log.actor_email).to eq('viewer@otterworks.com')
    end
  end

  describe 'cross-owner access' do
    let(:owner_id) { SecureRandom.uuid }
    let(:other_id) { SecureRandom.uuid }
    let!(:other_quota) { create(:storage_quota, user_id: other_id, tier: 'free') }
    let(:owner_headers) { auth_headers(user_id: owner_id, email: 'owner@otterworks.com') }

    it 'currently lets one caller read a quota belonging to a different user' do
      get "/api/v1/admin/quotas/#{other_id}", headers: owner_headers

      expect(response).to have_http_status(:ok)
      expect(json_body['user_id']).to eq(other_id)
    end

    # FINDING (documented, not fixed): there is no ownership or tenant scoping in
    # admin-service. Every record is global; `jwt.user_id` is recorded in the audit
    # log but never used to scope a lookup.
    it "should refuse caller A mutating caller B's storage quota" do
      pending('no ownership scoping: StorageQuota is looked up by params[:user_id] alone')

      put "/api/v1/admin/quotas/#{other_id}",
          params: { quota: { tier: 'enterprise', quota_bytes: 1_099_511_627_776 } },
          headers: owner_headers
      expect(response).to have_http_status(:forbidden)
    end

    it "should refuse caller A soft-deleting caller B's admin user record" do
      pending('no ownership scoping: AdminUser.find(params[:id]) is unscoped')

      delete "/api/v1/admin/users/#{user.id}", headers: owner_headers
      expect(response).to have_http_status(:forbidden)
    end

    it 'returns 404 for a quota that does not exist for any user' do
      get "/api/v1/admin/quotas/#{SecureRandom.uuid}", headers: owner_headers

      expect(response).to have_http_status(:not_found)
      expect(json_body['error']).to eq('Resource not found')
    end
  end
end
