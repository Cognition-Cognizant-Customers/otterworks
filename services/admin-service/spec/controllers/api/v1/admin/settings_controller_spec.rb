require 'rails_helper'

# The auto-investigate toggle (WP-10). AdminSettingsService talks to Redis, so
# it is stubbed here — these specs are about the controller's parameter
# handling, which is where the edges are.
RSpec.describe Api::V1::Admin::SettingsController do
  before do
    set_jwt_env(request)
    allow(AdminSettingsService).to receive(:auto_investigate_enabled?).and_return(true)
    allow(AdminSettingsService).to receive(:set_auto_investigate)
  end

  describe 'GET #auto_investigate' do
    it 'reports the current value' do
      get :auto_investigate
      expect(response.parsed_body).to eq('enabled' => true)
    end

    it 'reports a disabled toggle' do
      allow(AdminSettingsService).to receive(:auto_investigate_enabled?).and_return(false)
      get :auto_investigate
      expect(response.parsed_body).to eq('enabled' => false)
    end
  end

  describe 'PUT #update_auto_investigate parameter casting' do
    {
      'true' => true,
      '1' => true,
      'on' => true,
      'false' => false,
      '0' => false,
      'off' => false
    }.each do |input, expected|
      it "casts #{input.inspect} to #{expected}" do
        put :update_auto_investigate, params: { enabled: input }
        expect(AdminSettingsService).to have_received(:set_auto_investigate).with(expected)
      end
    end

    it 'rejects a request with no enabled parameter' do
      put :update_auto_investigate
      expect(response).to have_http_status(:bad_request)
    end

    it 'names the missing parameter' do
      put :update_auto_investigate
      expect(response.parsed_body['error']).to eq('Missing required parameter: enabled')
    end

    it 'writes nothing when the parameter is missing' do
      put :update_auto_investigate
      expect(AdminSettingsService).not_to have_received(:set_auto_investigate)
    end

    it 'treats an unrecognised value as true, because Boolean casting only knows false-y strings' do
      # Pinned, not endorsed: "maybe" is not in the false list, so it enables.
      put :update_auto_investigate, params: { enabled: 'maybe' }
      expect(AdminSettingsService).to have_received(:set_auto_investigate).with(true)
    end

    it 'returns the value read back from the store, not the value submitted' do
      allow(AdminSettingsService).to receive(:auto_investigate_enabled?).and_return(false)
      put :update_auto_investigate, params: { enabled: 'true' }
      expect(response.parsed_body).to eq('enabled' => false)
    end

    it 'surfaces a store failure as a 500 rather than reporting success' do
      allow(AdminSettingsService).to receive(:set_auto_investigate).and_raise(Redis::BaseError, 'redis down')
      put :update_auto_investigate, params: { enabled: 'true' }
      expect(response).to have_http_status(:internal_server_error)
    end
  end
end
