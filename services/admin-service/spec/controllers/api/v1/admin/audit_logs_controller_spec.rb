require 'rails_helper'

RSpec.describe Api::V1::Admin::AuditLogsController do
  before { set_jwt_env(request) }

  describe 'GET #index' do
    let!(:logs) { create_list(:audit_log, 3) }

    it 'returns paginated audit logs' do
      get :index
      expect(response).to have_http_status(:ok)
      body = JSON.parse(response.body)
      expect(body['audit_logs'].length).to eq(3)
    end

    it 'filters by action type' do
      action = 'feature_flag.created'
      create(:audit_log, action: action)
      get :index, params: { action_type: action }
      body = JSON.parse(response.body)
      expect(body['audit_logs'].all? { |l| l['action'] == action }).to be true
    end

    it 'filters by resource type' do
      get :index, params: { resource_type: 'AdminUser' }
      body = JSON.parse(response.body)
      expect(body['audit_logs'].all? { |l| l['resource_type'] == 'AdminUser' }).to be true
    end
  end

  describe 'GET #show' do
    let(:log) { create(:audit_log) }

    it 'returns the audit log entry' do
      get :show, params: { id: log.id }
      expect(response).to have_http_status(:ok)
      body = JSON.parse(response.body)
      expect(body['id']).to eq(log.id)
    end
  end
end
