require 'rails_helper'

# Quota API boundary coverage (WP-10): the over-quota flag the API exposes is
# the `used_bytes >= quota_bytes` rule, so the three interesting requests set
# the quota just above, exactly at, and just below current usage.
RSpec.describe Api::V1::Admin::QuotasController do
  before { set_jwt_env(request) }

  let(:user_id) { SecureRandom.uuid }
  let(:used) { 1_000 }
  let!(:quota) { create(:storage_quota, user_id: user_id, quota_bytes: 10_000, used_bytes: used) }
  let(:body) { response.parsed_body }

  describe 'GET #show over_quota at the boundary' do
    it 'reports under quota when usage is one byte below the limit' do
      quota.update!(quota_bytes: used + 1)
      get :show, params: { user_id: user_id }
      expect(body['over_quota']).to be false
    end

    it 'reports over quota when usage exactly equals the limit' do
      quota.update!(quota_bytes: used)
      get :show, params: { user_id: user_id }
      expect(body['over_quota']).to be true
    end

    it 'reports over quota when usage exceeds the limit' do
      quota.update!(quota_bytes: used - 1)
      get :show, params: { user_id: user_id }
      expect(body['over_quota']).to be true
    end

    it 'reports remaining bytes of 0 at the limit' do
      quota.update!(quota_bytes: used)
      get :show, params: { user_id: user_id }
      expect(body['remaining_bytes']).to eq(0)
    end

    it 'reports 100% usage at the limit' do
      quota.update!(quota_bytes: used)
      get :show, params: { user_id: user_id }
      expect(body['usage_percentage'].to_f).to eq(100.0)
    end
  end

  describe 'PUT #update quota_bytes validation boundary' do
    it 'rejects a negative quota' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: -1 } }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'rejects a quota of exactly 0' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'explains why a quota of 0 was rejected' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }
      expect(body['details']).to include('Quota bytes must be greater than 0')
    end

    it 'accepts the smallest legal quota of 1 byte' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 1 } }
      expect(response).to have_http_status(:ok)
    end

    it 'leaves the stored quota unchanged when validation fails' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }
      expect(quota.reload.quota_bytes).to eq(10_000)
    end

    it 'rejects a non-numeric quota' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 'unlimited' } }
      expect(response).to have_http_status(:unprocessable_entity)
    end
  end

  describe 'PUT #update tier negatives' do
    it 'rejects an unknown tier' do
      put :update, params: { user_id: user_id, quota: { tier: 'platinum' } }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'rejects a blank tier' do
      put :update, params: { user_id: user_id, quota: { tier: '' } }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'does not enforce the tier limit table — tier and quota_bytes may disagree' do
      # Pinned, not endorsed: nothing checks quota_bytes against TIER_LIMITS.
      put :update, params: { user_id: user_id, quota: { tier: 'enterprise', quota_bytes: 1 } }
      expect(response).to have_http_status(:ok)
      expect(quota.reload.quota_bytes).to eq(1)
    end
  end

  describe 'PUT #update parameter handling' do
    it 'rejects a request with no quota object' do
      put :update, params: { user_id: user_id }
      expect(response).to have_http_status(:bad_request)
    end

    it 'names the missing parameter' do
      put :update, params: { user_id: user_id }
      expect(body['error']).to eq('Missing parameter: quota')
    end

    it 'ignores an attempt to set used_bytes through the API' do
      put :update, params: { user_id: user_id, quota: { tier: 'pro', used_bytes: 99 } }
      expect(quota.reload.used_bytes).to eq(used)
    end

    it 'returns 404 when updating a quota for an unknown user' do
      put :update, params: { user_id: SecureRandom.uuid, quota: { tier: 'pro' } }
      expect(response).to have_http_status(:not_found)
    end
  end

  describe 'PUT #update audit logging' do
    it 'records an audit entry on success' do
      expect { put :update, params: { user_id: user_id, quota: { quota_bytes: 20_000 } } }
        .to change { AuditLog.by_action('quota.updated').count }.by(1)
    end

    it 'records the before and after quota in the audit entry' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 20_000 } }
      entry = AuditLog.by_action('quota.updated').last
      expect(entry.changes_made['before']).to include('quota_bytes' => 10_000)
      expect(entry.changes_made['after']).to include('quota_bytes' => 20_000)
    end

    it 'records no audit entry when validation fails' do
      expect { put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } } }
        .not_to(change { AuditLog.count })
    end
  end
end
