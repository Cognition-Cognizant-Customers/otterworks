require 'rails_helper'

# API-level boundary coverage for storage quotas: validation rejections, the
# over-quota boundary as rendered by the serializer, and repeated mutations.
RSpec.describe Api::V1::Admin::QuotasController do
  before { set_jwt_env(request) }

  let(:user_id) { SecureRandom.uuid }
  let(:body) { JSON.parse(response.body) }

  describe 'PUT #update quota_bytes boundaries' do
    let!(:quota) { create(:storage_quota, user_id: user_id, quota_bytes: 1_000, used_bytes: 500) }

    it 'rejects a quota of 0 with the validation message' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['error']).to eq('Validation failed')
      expect(body['details']).to include('Quota bytes must be greater than 0')
      expect(quota.reload.quota_bytes).to eq(1_000)
    end

    it 'rejects a negative quota' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: -1 } }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['details']).to include('Quota bytes must be greater than 0')
      expect(quota.reload.quota_bytes).to eq(1_000)
    end

    it 'accepts a quota of 1, the smallest allowed value' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 1 } }

      expect(response).to have_http_status(:ok)
      expect(quota.reload.quota_bytes).to eq(1)
    end

    it 'accepts a quota above the current usage' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 2_000 } }

      expect(response).to have_http_status(:ok)
      expect(body['over_quota']).to be false
    end

    it 'writes no audit log when the update is rejected' do
      expect do
        put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }
      end.not_to change(AuditLog, :count)
    end

    it 'ignores used_bytes, which is not a permitted parameter' do
      put :update, params: { user_id: user_id, quota: { used_bytes: 999_999, quota_bytes: 2_000 } }

      expect(response).to have_http_status(:ok)
      expect(quota.reload.used_bytes).to eq(500)
    end

    it 'rejects an unknown tier without changing the stored tier' do
      put :update, params: { user_id: user_id, quota: { tier: 'platinum' } }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['details']).to include('Tier is not included in the list')
      expect(quota.reload.tier).to eq('free')
    end

    it 'returns 400 when the quota parameter is missing entirely' do
      put :update, params: { user_id: user_id }

      expect(response).to have_http_status(:bad_request)
      expect(body['error']).to eq('Missing parameter: quota')
    end

    it 'returns 404 when the user has no quota row' do
      put :update, params: { user_id: SecureRandom.uuid, quota: { quota_bytes: 10 } }

      expect(response).to have_http_status(:not_found)
    end
  end

  describe 'GET #show over-quota boundary trio' do
    it 'reports under quota one byte below the limit' do
      create(:storage_quota, user_id: user_id, quota_bytes: 1_000, used_bytes: 999)
      get :show, params: { user_id: user_id }

      expect(body['over_quota']).to be false
      expect(body['remaining_bytes']).to eq(1)
      expect(body['usage_percentage']).to eq(99.9)
    end

    it 'reports over quota exactly at the limit' do
      create(:storage_quota, user_id: user_id, quota_bytes: 1_000, used_bytes: 1_000)
      get :show, params: { user_id: user_id }

      expect(body['over_quota']).to be true
      expect(body['remaining_bytes']).to eq(0)
      expect(body['usage_percentage']).to eq(100.0)
    end

    it 'reports over quota one byte above the limit' do
      create(:storage_quota, user_id: user_id, quota_bytes: 1_000, used_bytes: 1_001)
      get :show, params: { user_id: user_id }

      expect(body['over_quota']).to be true
      expect(body['remaining_bytes']).to eq(0)
      expect(body['usage_percentage']).to eq(100.1)
    end
  end

  describe 'repeated quota mutations' do
    let!(:quota) { create(:storage_quota, user_id: user_id, quota_bytes: 1_000, used_bytes: 500) }

    it 'is idempotent in effect when the same update is submitted twice' do
      2.times { put :update, params: { user_id: user_id, quota: { tier: 'pro', quota_bytes: 5_000 } } }

      expect(response).to have_http_status(:ok)
      expect(quota.reload.quota_bytes).to eq(5_000)
      expect(quota.tier).to eq('pro')
      expect(StorageQuota.where(user_id: user_id).count).to eq(1)
    end

    it 'appends an audit log entry for each submission' do
      expect do
        2.times { put :update, params: { user_id: user_id, quota: { quota_bytes: 5_000 } } }
      end.to change { AuditLog.by_action('quota.updated').count }.by(2)
    end

    it 'records the before and after values in the audit log' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 5_000 } }

      log = AuditLog.by_action('quota.updated').last
      expect(log.changes_made.dig('before', 'quota_bytes')).to eq(1_000)
      expect(log.changes_made.dig('after', 'quota_bytes')).to eq(5_000)
    end
  end
end
