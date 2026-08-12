require 'rails_helper'

RSpec.describe Api::V1::Admin::IncidentsController do
  before { set_jwt_env(request) }

  describe 'GET #index' do
    let!(:incident) { create(:incident) }

    it 'returns incidents and supports status filtering' do
      create(:incident, status: 'resolved')
      get :index, params: { status: 'open' }
      expect(response).to have_http_status(:ok)
      body = JSON.parse(response.body)
      expect(body['incidents'].length).to eq(1)
      expect(body['incidents'][0]['id']).to eq(incident.id)
      expect(body['total']).to eq(1)
    end
  end

  describe 'GET #show' do
    it 'returns an incident and refreshes an active Devin session' do
      incident = create(:incident, status: 'investigating', devin_session_id: 'sess-1')
      allow(DevinSessionService).to receive(:get_session).with(session_id: 'sess-1')
        .and_return({ status: 'completed', url: 'https://devin/session' })
      get :show, params: { id: incident.id }
      expect(response).to have_http_status(:ok)
      expect(JSON.parse(response.body).slice('id', 'devin_session_status'))
        .to eq('id' => incident.id, 'devin_session_status' => 'completed')
    end

    it 'returns not found for an unknown incident' do
      get :show, params: { id: SecureRandom.uuid }
      expect(response).to have_http_status(:not_found)
    end
  end

  describe 'POST #create' do
    let(:params) do
      { incident: { title: 'Database outage', description: 'Queries fail',
                    severity: 'critical', affected_service: 'auth-service' } }
    end

    it 'creates an incident and records a Devin session' do
      allow(DevinSessionService).to receive(:create_session)
        .and_return({ session_id: 'sess-2', url: 'https://devin/sess-2' })
      expect { post :create, params: params }.to change(Incident, :count).by(1)
      expect(response).to have_http_status(:created)
      body = JSON.parse(response.body)
      expect(body['status']).to eq('investigating')
      expect(body['devin_session_id']).to eq('sess-2')
      expect(AuditLog.where(action: 'incident.created')).to exist
    end

    it 'returns validation errors' do
      post :create, params: { incident: { title: '', description: '', severity: 'bad' } }
      expect(response).to have_http_status(:unprocessable_entity)
      expect(JSON.parse(response.body)['error']).to eq('Validation failed')
    end
  end

  describe 'PUT #update' do
    it 'transitions an incident and records an audit event' do
      incident = create(:incident, status: 'open')
      put :update, params: { id: incident.id, incident: { status: 'investigating' } }
      expect(response).to have_http_status(:ok)
      expect(incident.reload.status).to eq('investigating')
      expect(JSON.parse(response.body)['status']).to eq('investigating')
    end

    it 'rejects an invalid transition' do
      incident = create(:incident, status: 'closed')
      put :update, params: { id: incident.id, incident: { status: 'open' } }
      expect(response).to have_http_status(:unprocessable_entity)
      expect(JSON.parse(response.body)['error']).to eq('Invalid status transition')
    end
  end

  describe 'DELETE #destroy' do
    it 'rejects incidents with an active Devin session' do
      incident = create(:incident, devin_session_id: 'sess-3', devin_session_status: 'running')
      delete :destroy, params: { id: incident.id }
      expect(response).to have_http_status(:conflict)
      expect(Incident.exists?(incident.id)).to be(true)
    end

    it 'deletes an incident without an active session' do
      incident = create(:incident)
      delete :destroy, params: { id: incident.id }
      expect(response).to have_http_status(:no_content)
      expect(Incident.exists?(incident.id)).to be(false)
    end
  end

  describe 'POST #trigger_session' do
    it 'creates and stores a Devin session' do
      incident = create(:incident)
      allow(DevinSessionService).to receive(:create_session)
        .and_return({ session_id: 'sess-4', url: 'https://devin/sess-4' })
      post :trigger_session, params: { id: incident.id }
      expect(response).to have_http_status(:ok)
      expect(incident.reload.devin_session_id).to eq('sess-4')
    end

    it 'rejects duplicate sessions and external failures' do
      existing = create(:incident, devin_session_id: 'existing')
      post :trigger_session, params: { id: existing.id }
      expect(response).to have_http_status(:unprocessable_entity)

      incident = create(:incident)
      allow(DevinSessionService).to receive(:create_session).and_return(nil)
      post :trigger_session, params: { id: incident.id }
      expect(response).to have_http_status(:service_unavailable)
    end
  end
end
