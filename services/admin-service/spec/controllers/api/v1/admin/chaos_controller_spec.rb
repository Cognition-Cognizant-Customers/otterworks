require 'rails_helper'

RSpec.describe Api::V1::Admin::ChaosController do
  before do
    set_jwt_env(request)
    stub_const('ENV', ENV.to_h.merge('CHAOS_SECRET' => 'secret'))
    allow(controller).to receive(:redis).and_return(redis)
  end
  let(:redis) { instance_double(Redis) }

  it 'rejects an invalid chaos secret' do
    request.headers['X-Chaos-Secret'] = 'wrong'
    post :trigger, params: { service: 'search-service', scenario: 'suggest_500' }
    expect(response).to have_http_status(:unauthorized)
  end

  it 'triggers a valid scenario and starts its probe' do
    request.headers['X-Chaos-Secret'] = 'secret'
    allow(redis).to receive(:setex)
    allow(ChaosProbeService).to receive(:start)
    post :trigger, params: { service: 'search-service', scenario: 'suggest_500' }
    expect(response).to have_http_status(:ok)
    expect(JSON.parse(response.body)).to include('status' => 'chaos_active',
                                                   'key' => 'chaos:search-service:suggest_500',
                                                   'expires_in' => 600)
    expect(redis).to have_received(:setex).with('chaos:search-service:suggest_500', 600, '1')
    expect(ChaosProbeService).to have_received(:start)
  end

  it 'rejects an invalid service/scenario pair' do
    request.headers['X-Chaos-Secret'] = 'secret'
    post :trigger, params: { service: 'search-service', scenario: 'wrong' }
    expect(response).to have_http_status(:unprocessable_entity)
  end

  it 'resets Redis flags and resolves active incidents' do
    request.headers['X-Chaos-Secret'] = 'secret'
    incident = create(:incident, status: 'investigating', affected_service: 'search-service')
    allow(redis).to receive(:keys).with('chaos:*').and_return(['chaos:search-service:suggest_500'])
    allow(redis).to receive(:del)
    delete :reset
    expect(response).to have_http_status(:ok)
    expect(incident.reload.status).to eq('resolved')
    expect(JSON.parse(response.body)['resolved_incidents']).to include(incident.id)
  end
end
