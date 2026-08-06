require 'rails_helper'

# Batch-shape and partial-failure behaviour as seen through the HTTP layer.
RSpec.describe Api::V1::Admin::BulkController do
  before { set_jwt_env(request) }

  let(:body) { JSON.parse(response.body) }

  # See spec/services/bulk_operations_partial_failure_spec.rb — corrupting the
  # stored email makes exactly one item fail its next validated save.
  def poison(user)
    user.update_column(:email, "not-an-email-#{user.id}")
    user
  end

  describe 'batch size boundaries' do
    # Rails prunes empty-array parameters before they reach the action, so an empty
    # batch surfaces as a missing parameter rather than the controller's own
    # "non-empty array" guard — that guard is only reachable for a non-array value.
    it 'returns 400 for an empty batch, reported as a missing parameter' do
      post :users, params: { operation: 'suspend', user_ids: [] }

      expect(response).to have_http_status(:bad_request)
      expect(body['error']).to eq('Missing parameter: user_ids')
    end

    it 'returns 400 with the non-empty-array message when user_ids is an object' do
      post :users, params: { operation: 'suspend', user_ids: { '0' => SecureRandom.uuid } }

      expect(response).to have_http_status(:bad_request)
      expect(body['error']).to eq('user_ids must be a non-empty array')
    end

    it 'returns 400 when user_ids is not an array' do
      user = create(:admin_user)
      post :users, params: { operation: 'suspend', user_ids: user.id }

      expect(response).to have_http_status(:bad_request)
      expect(user.reload.status).to eq('active')
    end

    it 'returns 400 when the operation parameter is missing' do
      post :users, params: { user_ids: [SecureRandom.uuid] }

      expect(response).to have_http_status(:bad_request)
      expect(body['error']).to eq('Missing parameter: operation')
    end

    it 'returns 400 when user_ids is missing' do
      post :users, params: { operation: 'suspend' }

      expect(response).to have_http_status(:bad_request)
      expect(body['error']).to eq('Missing parameter: user_ids')
    end

    it 'processes a batch of exactly one' do
      user = create(:admin_user)
      post :users, params: { operation: 'suspend', user_ids: [user.id] }

      expect(response).to have_http_status(:ok)
      expect(body['success_count']).to eq(1)
    end

    it 'accepts a batch larger than the 100-row listing cap' do
      ids = create_list(:admin_user, 101).map(&:id)
      post :users, params: { operation: 'suspend', user_ids: ids }

      expect(response).to have_http_status(:ok)
      expect(body['success_count']).to eq(101)
    end
  end

  describe 'partial failure' do
    let(:users) { create_list(:admin_user, 5).sort_by(&:id) }

    before { poison(users[2]) }

    it 'returns 207 Multi-Status with the mixed counts' do
      post :users, params: { operation: 'suspend', user_ids: users.map(&:id) }

      expect(response).to have_http_status(:multi_status)
      expect(body['success_count']).to eq(4)
      expect(body['failure_count']).to eq(1)
    end

    it 'names the failing item in the errors array' do
      post :users, params: { operation: 'suspend', user_ids: users.map(&:id) }

      expect(body['errors'].map { |e| e['user_id'] }).to contain_exactly(users[2].id)
    end

    it 'keeps the successful items committed (the batch is not rolled back)' do
      post :users, params: { operation: 'suspend', user_ids: users.map(&:id) }

      statuses = users.map { |u| u.reload.status }
      expect(statuses.count('suspended')).to eq(4)
      expect(users[2].reload.status).to eq('active')
    end
  end

  describe 'total failure' do
    it 'returns 422 when every item fails' do
      users = create_list(:admin_user, 2)
      users.each { |u| poison(u) }

      post :users, params: { operation: 'suspend', user_ids: users.map(&:id) }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['success_count']).to eq(0)
      expect(body['failure_count']).to eq(2)
    end

    it 'returns 422 when none of the ids exist' do
      post :users, params: { operation: 'suspend', user_ids: [SecureRandom.uuid] }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['failure_count']).to eq(1)
    end

    it 'returns 400 for an unknown operation' do
      post :users, params: { operation: 'obliterate', user_ids: [create(:admin_user).id] }

      expect(response).to have_http_status(:bad_request)
      expect(body['errors']).to include('Invalid operation: obliterate')
    end
  end

  describe 'idempotency' do
    let(:users) { create_list(:admin_user, 3) }

    it 'returns the same result when the same batch is submitted twice' do
      2.times { post :users, params: { operation: 'suspend', user_ids: users.map(&:id) } }

      expect(response).to have_http_status(:ok)
      expect(body['success_count']).to eq(3)
      expect(body['failure_count']).to eq(0)
      expect(users.map { |u| u.reload.status }.uniq).to eq(['suspended'])
    end

    it 'records one audit entry per submission' do
      expect do
        2.times { post :users, params: { operation: 'suspend', user_ids: users.map(&:id) } }
      end.to change { AuditLog.by_action('bulk.users_updated').count }.by(2)
    end
  end
end
