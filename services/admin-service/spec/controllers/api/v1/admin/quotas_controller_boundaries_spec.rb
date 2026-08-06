require 'rails_helper'

RSpec.describe Api::V1::Admin::QuotasController do
  before { set_jwt_env(request, user_id: actor_id, email: actor_email) }

  let(:actor_id) { SecureRandom.uuid }
  let(:actor_email) { 'quota-admin@otterworks.com' }
  let(:user_id) { SecureRandom.uuid }
  let!(:quota) { create(:storage_quota, user_id: user_id, quota_bytes: 5_368_709_120, tier: 'free') }

  def body
    response.parsed_body
  end

  describe 'PUT #update with an invalid quota_bytes' do
    it 'update_quota_bytes_zero_returns_422_with_details' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['details']).to include('Quota bytes must be greater than 0')
    end

    it 'update_quota_bytes_negative_returns_422_with_details' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: -1 } }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(body['details']).to include('Quota bytes must be greater than 0')
    end

    it 'update_quota_bytes_invalid_leaves_the_stored_quota_unchanged' do
      put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }

      expect(quota.reload.quota_bytes).to eq(5_368_709_120)
    end

    it 'update_quota_bytes_invalid_writes_no_audit_log' do
      expect do
        put :update, params: { user_id: user_id, quota: { quota_bytes: 0 } }
      end.not_to change(AuditLog, :count)
    end
  end

  describe 'missing quota row' do
    let(:unknown_user_id) { SecureRandom.uuid }

    it 'show_user_id_without_quota_row_returns_404' do
      get :show, params: { user_id: unknown_user_id }

      expect(response).to have_http_status(:not_found)
      expect(body['error']).to eq('Resource not found')
    end

    it 'update_user_id_without_quota_row_returns_404' do
      put :update, params: { user_id: unknown_user_id, quota: { quota_bytes: 1_000 } }

      expect(response).to have_http_status(:not_found)
    end

    it 'update_user_id_without_quota_row_creates_no_quota_row' do
      expect do
        put :update, params: { user_id: unknown_user_id, quota: { quota_bytes: 1_000 } }
      end.not_to change(StorageQuota, :count)
    end
  end

  describe 'audit trail' do
    it 'update_successful_writes_one_quota_updated_audit_log_with_before_and_after' do
      expect do
        put :update, params: { user_id: user_id, quota: { tier: 'pro', quota_bytes: 214_748_364_800 } }
      end.to change(AuditLog, :count).by(1)

      log = AuditLog.order(:created_at).last
      expect(log.action).to eq('quota.updated')
      expect(log.resource_type).to eq('StorageQuota')
      expect(log.resource_id).to eq(quota.id)
      expect(log.changes_made['before']).to eq('quota_bytes' => 5_368_709_120, 'tier' => 'free')
      expect(log.changes_made['after']).to eq('quota_bytes' => 214_748_364_800, 'tier' => 'pro')
    end

    it 'update_successful_records_the_jwt_identity_as_the_actor' do
      put :update, params: { user_id: user_id, quota: { tier: 'pro' } }

      log = AuditLog.order(:created_at).last
      expect(log.actor_id).to eq(actor_id)
      expect(log.actor_email).to eq(actor_email)
    end
  end

  describe 'PUT #update that changes nothing' do
    let(:no_op_params) { { user_id: user_id, quota: { tier: quota.tier, quota_bytes: quota.quota_bytes } } }

    it 'update_with_identical_values_returns_200' do
      put :update, params: no_op_params

      expect(response).to have_http_status(:ok)
      expect(body['tier']).to eq('free')
    end

    it 'update_with_identical_values_does_not_touch_updated_at' do
      original_updated_at = quota.reload.updated_at

      travel_to(1.hour.from_now) { put :update, params: no_op_params }

      expect(quota.reload.updated_at).to eq(original_updated_at)
    end

    it 'update_with_identical_values_still_writes_an_audit_log_with_equal_before_and_after' do
      expect { put :update, params: no_op_params }.to change(AuditLog, :count).by(1)

      log = AuditLog.order(:created_at).last
      expect(log.changes_made['before']).to eq(log.changes_made['after'])
    end
  end
end
