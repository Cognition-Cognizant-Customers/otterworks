require 'rails_helper'

RSpec.describe Api::V1::Admin::SettingsController do
  before { set_jwt_env(request) }

  it 'returns the auto-investigate setting' do
    allow(AdminSettingsService).to receive(:auto_investigate_enabled?).and_return(false)
    get :auto_investigate
    expect(response).to have_http_status(:ok)
    expect(JSON.parse(response.body)).to eq('enabled' => false)
  end

  it 'updates and returns the setting' do
    allow(AdminSettingsService).to receive(:set_auto_investigate)
    allow(AdminSettingsService).to receive(:auto_investigate_enabled?).and_return(true)
    put :update_auto_investigate, params: { enabled: 'true' }
    expect(response).to have_http_status(:ok)
    expect(JSON.parse(response.body)).to eq('enabled' => true)
    expect(AdminSettingsService).to have_received(:set_auto_investigate).with(true)
  end

  it 'rejects a missing setting' do
    put :update_auto_investigate
    expect(response).to have_http_status(:bad_request)
  end
end
