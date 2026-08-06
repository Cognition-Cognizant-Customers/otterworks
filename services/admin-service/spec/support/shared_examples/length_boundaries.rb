RSpec.shared_examples 'a 255 character maximum attribute' do |factory_name, attribute|
  subject(:record) { build(factory_name, attribute => value) }

  context 'with 254 characters' do
    let(:value) { 'a' * 254 }

    it "#{factory_name}_#{attribute}_254_characters_is_valid" do
      expect(record).to be_valid
    end
  end

  context 'with exactly 255 characters' do
    let(:value) { 'a' * 255 }

    it "#{factory_name}_#{attribute}_255_characters_is_valid" do
      expect(record).to be_valid
    end

    it "#{factory_name}_#{attribute}_255_characters_persists_unchanged" do
      record.save!

      expect(record.reload.public_send(attribute)).to eq(value)
    end
  end

  context 'with 256 characters' do
    let(:value) { 'a' * 256 }

    it "#{factory_name}_#{attribute}_256_characters_is_invalid" do
      expect(record).to be_invalid
      expect(record.errors[attribute]).to include('is too long (maximum is 255 characters)')
    end
  end

  # Pins the unit the validator counts: 255 two-byte characters is 510 bytes and
  # is accepted, so the maximum is 255 characters, not 255 bytes.
  context 'with 255 multibyte characters (510 bytes)' do
    let(:value) { 'é' * 255 }

    it "#{factory_name}_#{attribute}_255_multibyte_characters_is_valid_because_the_limit_counts_characters" do
      expect(value.length).to eq(255)
      expect(value.bytesize).to eq(510)
      expect(record).to be_valid
    end

    it "#{factory_name}_#{attribute}_255_multibyte_characters_persists_unchanged" do
      record.save!

      expect(record.reload.public_send(attribute)).to eq(value)
    end

    it "#{factory_name}_#{attribute}_256_multibyte_characters_is_invalid" do
      oversized = build(factory_name, attribute => 'é' * 256)

      expect(oversized).to be_invalid
      expect(oversized.errors[attribute]).to include('is too long (maximum is 255 characters)')
    end
  end
end
