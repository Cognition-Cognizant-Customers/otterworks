require 'rails_helper'

RSpec.describe HealthChecker do
  describe '.check_service' do
    it 'reports a healthy HTTP service and latency' do
      response = instance_double(Net::HTTPResponse, code: '200')
      http = instance_double(Net::HTTP)
      allow(Net::HTTP).to receive(:new).and_return(http)
      allow(http).to receive(:open_timeout=)
      allow(http).to receive(:read_timeout=)
      allow(http).to receive(:get).and_return(response)
      result = described_class.check_service('file-service')
      expect(result.status).to eq('healthy')
      expect(result.latency_ms).to be >= 0
    end

    it 'reports non-200 and timeout failures' do
      response = instance_double(Net::HTTPResponse, code: '503')
      http = instance_double(Net::HTTP)
      allow(Net::HTTP).to receive(:new).and_return(http)
      allow(http).to receive(:open_timeout=)
      allow(http).to receive(:read_timeout=)
      allow(http).to receive(:get).and_return(response)
      expect(described_class.check_service('file-service').status).to eq('unhealthy')
      allow(http).to receive(:get).and_raise(Timeout::Error, 'timed out')
      result = described_class.check_service('file-service')
      expect(result.status).to eq('unhealthy')
      expect(result.message).to eq('timed out')
    end
  end

  describe '.check_all' do
    it 'aggregates service, database, and Redis status' do
      healthy = described_class::ServiceStatus.new(name: 'x', status: 'healthy', latency_ms: 1)
      allow(described_class).to receive(:check_service).and_return(healthy)
      allow(described_class).to receive(:check_database).and_return(status: 'healthy')
      allow(described_class).to receive(:check_redis).and_return(status: 'unhealthy', message: 'down')
      result = described_class.check_all
      expect(result[:status]).to eq('healthy')
      expect(result[:services]).to all(include(status: 'healthy'))
      expect(result[:redis]).to include(status: 'unhealthy')
    end
  end
end
