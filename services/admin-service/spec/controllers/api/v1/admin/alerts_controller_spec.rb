require 'rails_helper'

RSpec.describe Api::V1::Admin::AlertsController do
  let(:payload) do
    { alerts: [{ status: 'firing',
                 labels: { alertname: 'FileErrors', severity: 'critical',
                           affected_service: 'file-service' },
                 annotations: { summary: 'Uploads failing', description: 'S3 errors',
                                 runbook_url: 'https://runbook' } }] }
  end

  before { stub_const('ENV', ENV.to_h.merge('ALERT_WEBHOOK_SECRET' => 'alert-secret')) }

  it 'rejects an invalid webhook secret' do
    request.headers['X-Alert-Secret'] = 'wrong'
    post :ingest, params: payload
    expect(response).to have_http_status(:unauthorized)
  end

  it 'creates an incident and triggers Devin for an accepted alert' do
    request.headers['X-Alert-Secret'] = 'alert-secret'
    allow(AdminSettingsService).to receive(:auto_investigate_enabled?).and_return(true)
    allow(DevinSessionService).to receive(:create_session)
      .and_return({ session_id: 'alert-session', url: 'https://devin/alert' })
    expect do
      post :ingest, params: payload
    end.to change(Incident, :count).by(1)
    expect(response).to have_http_status(:ok)
    body = JSON.parse(response.body)
    expect(body).to include('received' => 1, 'processed' => 1)
    incident = Incident.order(:created_at).last
    expect(incident.description).to include('**Runbook**: https://runbook')
    expect(incident.devin_session_id).to eq('alert-session')
  end

  it 'returns bad request without an alerts array' do
    request.headers['X-Alert-Secret'] = 'alert-secret'
    post :ingest, params: {}
    expect(response).to have_http_status(:bad_request)
  end

  it 'deduplicates firing alerts and resolves resolved alerts' do
    request.headers['X-Alert-Secret'] = 'alert-secret'
    existing = create(:incident, status: 'investigating', affected_service: 'file-service')
    post :ingest, params: payload
    expect(JSON.parse(response.body)['incidents'][0]['skipped']).to be(true)

    post :ingest, params: { alerts: [{ status: 'resolved',
      labels: { alertname: 'FileErrors', affected_service: 'file-service' } }] }
    expect(response).to have_http_status(:ok)
    expect(existing.reload.status).to eq('resolved')
  end
end
