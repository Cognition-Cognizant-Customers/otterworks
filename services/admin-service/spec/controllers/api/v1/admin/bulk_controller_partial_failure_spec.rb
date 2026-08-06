require 'rails_helper'

# HTTP status matrix for bulk operations (WP-10). The controller maps the
# service result onto 200 / 207 / 422 / 400; each quadrant is pinned here.
RSpec.describe Api::V1::Admin::BulkController do
  before { set_jwt_env(request) }

  def poison_user
    create(:admin_user).tap { |u| u.update_column(:email, "not-an-email-#{u.id}") }
  end

  describe 'POST #users status matrix' do
    let!(:healthy) { create_list(:admin_user, 2) }

    it 'returns 200 when every record succeeds' do
      post :users, params: { operation: 'suspend', user_ids: healthy.map(&:id) }
      expect(response).to have_http_status(:ok)
    end

    it 'returns 207 when some records succeed and some fail' do
      post :users, params: { operation: 'update_role', role: 'editor',
                             user_ids: healthy.map(&:id) + [poison_user.id] }
      expect(response).to have_http_status(:multi_status)
    end

    it 'returns 422 when every record fails' do
      post :users, params: { operation: 'update_role', role: 'editor', user_ids: [poison_user.id] }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'returns 422 when every id is unknown' do
      post :users, params: { operation: 'suspend', user_ids: [SecureRandom.uuid] }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'returns 400 for an unknown operation' do
      post :users, params: { operation: 'obliterate', user_ids: healthy.map(&:id) }
      expect(response).to have_http_status(:bad_request)
    end
  end

  describe 'POST #users response body' do
    let!(:healthy) { create_list(:admin_user, 2) }
    let(:body) { response.parsed_body }

    it 'echoes the requested operation' do
      post :users, params: { operation: 'suspend', user_ids: healthy.map(&:id) }
      expect(body['operation']).to eq('suspend')
    end

    it 'reports both counts on a partial failure' do
      post :users, params: { operation: 'update_role', role: 'editor',
                             user_ids: healthy.map(&:id) + [poison_user.id] }
      expect(body.values_at('success_count', 'failure_count')).to eq([2, 1])
    end

    it 'lists per-record errors on a partial failure' do
      broken = poison_user
      post :users, params: { operation: 'update_role', role: 'editor',
                             user_ids: healthy.map(&:id) + [broken.id] }
      expect(body['errors'].first).to include('user_id' => broken.id)
    end

    it 'returns an empty error list on full success' do
      post :users, params: { operation: 'suspend', user_ids: healthy.map(&:id) }
      expect(body['errors']).to eq([])
    end

    it 'does not roll back the successful half of a partial failure' do
      post :users, params: { operation: 'update_role', role: 'editor',
                             user_ids: healthy.map(&:id) + [poison_user.id] }
      expect(healthy.map { |u| u.reload.role }).to all(eq('editor'))
    end
  end

  describe 'POST #users parameter negatives' do
    let!(:user) { create(:admin_user) }

    it 'rejects a missing operation' do
      post :users, params: { user_ids: [user.id] }
      expect(response).to have_http_status(:bad_request)
    end

    it 'names the missing operation parameter' do
      post :users, params: { user_ids: [user.id] }
      expect(response.parsed_body['error']).to eq('Missing parameter: operation')
    end

    it 'rejects missing user_ids' do
      post :users, params: { operation: 'suspend' }
      expect(response).to have_http_status(:bad_request)
    end

    it 'rejects an empty user_ids array' do
      post :users, params: { operation: 'suspend', user_ids: [] }
      expect(response).to have_http_status(:bad_request)
    end

    it 'rejects a scalar user_ids value' do
      post :users, params: { operation: 'suspend', user_ids: user.id }
      expect(response).to have_http_status(:bad_request)
    end

    it 'explains that user_ids must be a non-empty array' do
      post :users, params: { operation: 'suspend', user_ids: user.id }
      expect(response.parsed_body['error']).to eq('user_ids must be a non-empty array')
    end
  end

  describe 'POST #users id-count boundary' do
    let!(:users) { create_list(:admin_user, 2) }

    it 'accepts a single id' do
      post :users, params: { operation: 'suspend', user_ids: [users.first.id] }
      expect(response.parsed_body['success_count']).to eq(1)
    end

    it 'accepts many ids in one call' do
      post :users, params: { operation: 'suspend', user_ids: users.map(&:id) }
      expect(response.parsed_body['success_count']).to eq(2)
    end

    it 'ignores an unpermitted parameter rather than mass-assigning it' do
      post :users, params: { operation: 'suspend', user_ids: [users.first.id], status: 'deleted' }
      expect(users.first.reload.status).to eq('suspended')
    end
  end
end
