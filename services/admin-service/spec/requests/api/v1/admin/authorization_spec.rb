require 'rails_helper'

# FINDING (documented, not fixed): the admin API authenticates a JWT at the Rack
# edge (JwtAuthenticator) but performs no authorization anywhere. No controller
# under /api/v1/admin/* inspects the caller's role, so any principal holding any
# signed token — including the lowest-privilege `viewer` role, or a role string
# the service does not even recognise — may perform every destructive action.
# These examples pin the CURRENT behaviour so a future change of policy is visible.
RSpec.describe 'Api::V1::Admin authorization' do
  let!(:target_user) { create(:admin_user) }

  describe 'unauthenticated requests' do
    it 'admin_users_index_without_token_returns_401' do
      get '/api/v1/admin/users'

      expect(response).to have_http_status(:unauthorized)
      expect(response.parsed_body['error']).to eq('Missing authorization token')
    end

    it 'admin_users_destroy_without_token_leaves_the_user_untouched' do
      delete "/api/v1/admin/users/#{target_user.id}"

      expect(response).to have_http_status(:unauthorized)
      expect(target_user.reload.status).to eq('active')
    end

    it 'admin_users_index_with_malformed_token_returns_401' do
      get '/api/v1/admin/users', headers: { 'Authorization' => 'Bearer not-a-jwt' }

      expect(response).to have_http_status(:unauthorized)
      expect(response.parsed_body['error']).to eq('Invalid or expired token')
    end
  end

  describe 'authenticated requests carrying a low-privilege role' do
    let(:viewer_headers) { auth_headers(role: 'viewer') }

    it 'admin_users_index_with_viewer_role_token_returns_200_because_no_role_check_exists' do
      get '/api/v1/admin/users', headers: viewer_headers

      expect(response).to have_http_status(:ok)
    end

    it 'admin_users_destroy_with_viewer_role_token_soft_deletes_the_user_because_no_role_check_exists' do
      delete "/api/v1/admin/users/#{target_user.id}", headers: viewer_headers

      expect(response).to have_http_status(:no_content)
      expect(target_user.reload.status).to eq('deleted')
    end

    it 'admin_users_suspend_with_unrecognised_role_token_succeeds_because_no_role_check_exists' do
      put "/api/v1/admin/users/#{target_user.id}/suspend",
          params: { reason: 'no authorization enforced' },
          headers: auth_headers(role: 'definitely-not-a-real-role')

      expect(response).to have_http_status(:ok)
      expect(target_user.reload.status).to eq('suspended')
    end
  end
end
