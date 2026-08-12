require 'rails_helper'

RSpec.describe AdminSettingsService do
  let(:redis) { instance_double(Redis) }

  before do
    allow(Redis).to receive(:new).and_return(redis)
    allow(redis).to receive(:close)
  end

  it 'defaults to enabled when Redis has no value' do
    allow(redis).to receive(:get).with(described_class::AUTO_INVESTIGATE_KEY).and_return(nil)
    expect(described_class.auto_investigate_enabled?).to be(true)
    expect(redis).to have_received(:close)
  end

  it 'reads false and writes a new setting' do
    allow(redis).to receive(:get).and_return('false')
    allow(redis).to receive(:set)
    expect(described_class.auto_investigate_enabled?).to be(false)
    described_class.set_auto_investigate(true)
    expect(redis).to have_received(:set).with(described_class::AUTO_INVESTIGATE_KEY, 'true')
  end

  it 'fails open on read errors and re-raises write errors' do
    allow(redis).to receive(:get).and_raise(Redis::CannotConnectError, 'down')
    expect(described_class.auto_investigate_enabled?).to be(true)
    allow(redis).to receive(:set).and_raise(Redis::CannotConnectError, 'down')
    expect { described_class.set_auto_investigate(false) }.to raise_error(Redis::CannotConnectError)
  end
end
