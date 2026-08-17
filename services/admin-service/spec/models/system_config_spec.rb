require 'rails_helper'

RSpec.describe SystemConfig do
  describe 'validations' do
    subject { build(:system_config) }

    it { is_expected.to validate_presence_of(:config_key) }
    it { is_expected.to validate_uniqueness_of(:config_key) }
    it { is_expected.to validate_presence_of(:value) }
    it { is_expected.to validate_presence_of(:value_type) }
    it { is_expected.to validate_inclusion_of(:value_type).in_array(described_class::VALUE_TYPES) }

    it 'requires snake_case config_key' do
      config = build(:system_config, config_key: 'Invalid Key')
      expect(config).not_to be_valid
    end
  end

  describe '#typed_value' do
    it 'returns integer for integer type' do
      config = build(:system_config, :integer_config)
      expect(config.typed_value).to eq(42)
    end

    it 'returns boolean for boolean type' do
      config = build(:system_config, :boolean_config)
      expect(config.typed_value).to be true
    end

    it 'returns parsed JSON for json type' do
      config = build(:system_config, :json_config)
      expect(config.typed_value).to eq({ 'key' => 'value' })
    end

    it 'returns string for string type' do
      config = build(:system_config, value: 'hello', value_type: 'string')
      expect(config.typed_value).to eq('hello')
    end
  end

  describe 'scopes' do
    let!(:public_config) { create(:system_config) }
    let!(:secret_config) { create(:system_config, :secret) }

    describe '.public_configs' do
      it 'excludes secret configs' do
        expect(described_class.public_configs).to include(public_config)
        expect(described_class.public_configs).not_to include(secret_config)
      end
    end
  end
end
