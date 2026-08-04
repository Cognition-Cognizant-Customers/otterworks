require 'rails_helper'

RSpec.describe Api::V1::Admin::IncidentsController do
  let(:valid_params) do
    {
      incident: {
        title: 'Document outage',
        description: 'Documents cannot be opened',
        severity: 'high',
        affected_service: 'document-service'
      }
    }
  end

  before do
    set_jwt_env(request, role: 'admin')
    allow(DevinSessionService).to receive_messages(
      create_session: { session_id: 'session-123', url: 'https://devin.ai/session-123' },
      get_session: nil
    )
  end

  describe 'authorization' do
    before { set_jwt_env(request, role: 'user') }

    it 'forbids every action' do
      incident = create(:incident)

      expect(get(:index)).to have_http_status(:forbidden)
      expect(get(:show, params: { id: incident.id })).to have_http_status(:forbidden)
      expect(post(:create, params: valid_params)).to have_http_status(:forbidden)
      expect(put(:update, params: { id: incident.id, incident: { status: 'resolved' } }))
        .to have_http_status(:forbidden)
      expect(delete(:destroy, params: { id: incident.id })).to have_http_status(:forbidden)
      expect(post(:trigger_session, params: { id: incident.id })).to have_http_status(:forbidden)
      expect(DevinSessionService).not_to have_received(:create_session)
    end

    it 'does not create an incident for a forbidden create' do
      expect do
        post :create, params: valid_params
      end.not_to change(Incident, :count)
    end
  end

  describe 'authorized actions' do
    it 'returns incidents from index' do
      create_list(:incident, 2)

      get :index

      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['total']).to eq(2)
    end

    it 'returns an incident from show' do
      incident = create(:incident)

      get :show, params: { id: incident.id }

      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['id']).to eq(incident.id)
    end

    it 'creates an incident' do
      expect do
        post :create, params: valid_params
      end.to change(Incident, :count).by(1)

      expect(response).to have_http_status(:created)
    end

    it 'updates an incident' do
      incident = create(:incident, status: 'investigating')

      put :update, params: { id: incident.id, incident: { status: 'resolved' } }

      expect(response).to have_http_status(:ok)
      expect(incident.reload.status).to eq('resolved')
    end

    it 'destroys an incident' do
      incident = create(:incident, status: 'closed')

      expect do
        delete :destroy, params: { id: incident.id }
      end.to change(Incident, :count).by(-1)

      expect(response).to have_http_status(:no_content)
    end

    it 'triggers a Devin session' do
      incident = create(:incident)

      post :trigger_session, params: { id: incident.id }

      expect(response).to have_http_status(:ok)
      expect(incident.reload.devin_session_id).to eq('session-123')
    end
  end

  it 'accepts uppercase admin roles' do
    set_jwt_env(request, role: 'ADMIN')

    get :index

    expect(response).to have_http_status(:ok)
  end

  it 'allows super admins' do
    set_jwt_env(request, role: 'super_admin')

    get :index

    expect(response).to have_http_status(:ok)
  end
end
