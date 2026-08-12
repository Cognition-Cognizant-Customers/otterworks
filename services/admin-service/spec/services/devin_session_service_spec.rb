require 'rails_helper'

RSpec.describe DevinSessionService do
  let(:incident) { create(:incident, title: 'S3 outage', severity: 'critical',
                          affected_service: 'file-service', description: 'Uploads fail') }

  it 'skips creation when credentials are absent' do
    hide_const('ENV')
    stub_const('ENV', {})
    expect(described_class.create_session(incident: incident)).to be_nil
  end

  it 'posts a prompt and parses a successful session response' do
    stub_const('ENV', ENV.to_h.merge('DEVIN_API_KEY' => 'key', 'DEVIN_ORG_ID' => 'org'))
    response = instance_double(Net::HTTPSuccess, body: { session_id: 's-1', url: 'https://devin/s-1' }.to_json)
    allow(described_class).to receive(:make_request).and_return(response)
    result = described_class.create_session(incident: incident)
    expect(result).to eq(session_id: 's-1', url: 'https://devin/s-1')
    expect(described_class).to have_received(:make_request) do |uri, request|
      expect(uri.to_s).to include('/v3/organizations/org/sessions')
      expect(request['Authorization']).to eq('Bearer key')
      expect(request.body).to include('S3 outage')
    end
  end

  it 'returns nil for API failures, malformed JSON, and get without credentials' do
    stub_const('ENV', ENV.to_h.merge('DEVIN_API_KEY' => 'key', 'DEVIN_ORG_ID' => 'org'))
    allow(described_class).to receive(:make_request).and_return(nil)
    expect(described_class.create_session(incident: incident)).to be_nil
    allow(described_class).to receive(:make_request)
      .and_return(instance_double(Net::HTTPSuccess, body: 'not-json'))
    expect(described_class.create_session(incident: incident)).to be_nil
    stub_const('ENV', {})
    expect(described_class.get_session(session_id: 's-1')).to be_nil
  end

  it 'parses session status and URL' do
    stub_const('ENV', ENV.to_h.merge('DEVIN_API_KEY' => 'key', 'DEVIN_ORG_ID' => 'org'))
    response = instance_double(Net::HTTPSuccess, body: { status_enum: 'completed', url: 'https://devin/s-1' }.to_json)
    allow(described_class).to receive(:make_request).and_return(response)
    expect(described_class.get_session(session_id: 's-1')).to eq(status: 'completed', url: 'https://devin/s-1')
  end
end
