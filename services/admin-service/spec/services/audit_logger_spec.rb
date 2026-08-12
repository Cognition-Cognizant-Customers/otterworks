require 'rails_helper'

RSpec.describe AuditLogger do
  it 'persists request actor and request metadata' do
    user_id = SecureRandom.uuid
    request = instance_double(ActionDispatch::Request, remote_ip: '127.0.0.1',
                              user_agent: 'RSpec', env: {
                                'jwt.user_id' => user_id, 'jwt.user_email' => 'admin@test'
                              })
    expect do
      described_class.log(action: 'incident.created', resource_type: 'Incident',
                          resource_id: 'incident-1', request: request,
                          changes_made: { status: 'open' })
    end.to change(AuditLog, :count).by(1)
    log = AuditLog.order(:created_at).last
    expect(log).to have_attributes(actor_id: user_id, actor_email: 'admin@test',
                                   ip_address: '127.0.0.1', user_agent: 'RSpec')
    expect(log.changes_made).to include('status' => 'open')
  end

  it 'swallows persistence failures and logs the error' do
    allow(AuditLog).to receive(:record!).and_raise(StandardError, 'database unavailable')
    expect(Rails.logger).to receive(:error).with(/database unavailable/)
    expect(described_class.log(action: 'x', resource_type: 'Y')).to be_nil
  end
end
