require 'rails_helper'

# Boundary-focused companion to spec/models/storage_quota_spec.rb.
# Pins the `>=` semantics of StorageQuota#over_quota? and the `.over_quota` scope,
# plus the rejection boundaries of the numericality validations.
RSpec.describe StorageQuota do
  let(:quota_bytes) { 1_000 }

  describe '#over_quota? boundary trio' do
    it 'is false when used_bytes is quota_bytes - 1' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes - 1)
      expect(quota.over_quota?).to be false
    end

    it 'is true when used_bytes equals quota_bytes exactly (>= not >)' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes)
      expect(quota.over_quota?).to be true
    end

    it 'is true when used_bytes is quota_bytes + 1' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes + 1)
      expect(quota.over_quota?).to be true
    end
  end

  describe '#remaining_bytes boundary trio' do
    it 'returns 1 at quota_bytes - 1' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes - 1)
      expect(quota.remaining_bytes).to eq(1)
    end

    it 'returns 0 at exactly quota_bytes' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes)
      expect(quota.remaining_bytes).to eq(0)
    end

    it 'clamps to 0 (never negative) at quota_bytes + 1' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes + 1)
      expect(quota.remaining_bytes).to eq(0)
    end
  end

  describe '#usage_percentage boundary trio' do
    it 'is just under 100 at quota_bytes - 1' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes - 1)
      expect(quota.usage_percentage).to eq(99.9)
    end

    it 'is exactly 100.0 at quota_bytes' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes)
      expect(quota.usage_percentage).to eq(100.0)
    end

    it 'exceeds 100 at quota_bytes + 1' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes + 1)
      expect(quota.usage_percentage).to eq(100.1)
    end

    it 'rounds to two decimal places' do
      quota = build(:storage_quota, quota_bytes: 3, used_bytes: 1)
      expect(quota.usage_percentage).to eq(33.33)
    end

    it 'is 0.0 when nothing is used' do
      quota = build(:storage_quota, quota_bytes: quota_bytes, used_bytes: 0)
      expect(quota.usage_percentage).to eq(0.0)
    end
  end

  describe '.over_quota scope boundary trio' do
    let!(:under) { create(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes - 1) }
    let!(:exactly_at) { create(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes) }
    let!(:over) { create(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes + 1) }

    it 'excludes a quota one byte under the limit' do
      expect(described_class.over_quota).not_to include(under)
    end

    it 'includes a quota exactly at the limit (SQL uses >=, matching #over_quota?)' do
      expect(described_class.over_quota).to include(exactly_at)
    end

    it 'includes a quota one byte over the limit' do
      expect(described_class.over_quota).to include(over)
    end

    it 'agrees with the in-memory predicate for every boundary row' do
      rows = [under, exactly_at, over]
      scoped_ids = described_class.over_quota.pluck(:id)
      expect(rows.select(&:over_quota?).map(&:id)).to match_array(scoped_ids)
    end
  end

  describe 'quota_bytes validation boundaries' do
    it 'rejects 0 with "must be greater than 0"' do
      quota = build(:storage_quota, quota_bytes: 0)
      expect(quota).not_to be_valid
      expect(quota.errors[:quota_bytes]).to include('must be greater than 0')
    end

    it 'rejects a negative value' do
      quota = build(:storage_quota, quota_bytes: -1)
      expect(quota).not_to be_valid
      expect(quota.errors[:quota_bytes]).to include('must be greater than 0')
    end

    it 'rejects nil' do
      quota = build(:storage_quota, quota_bytes: nil)
      expect(quota).not_to be_valid
      expect(quota.errors[:quota_bytes]).to include("can't be blank")
    end

    it 'accepts 1, the smallest allowed quota' do
      quota = build(:storage_quota, quota_bytes: 1, used_bytes: 0)
      expect(quota).to be_valid
    end

    it 'refuses to persist a zero quota' do
      quota = build(:storage_quota, quota_bytes: 0)
      expect { quota.save! }.to raise_error(ActiveRecord::RecordInvalid, /Quota bytes must be greater than 0/)
    end
  end

  describe 'used_bytes validation boundaries' do
    it 'rejects -1' do
      quota = build(:storage_quota, used_bytes: -1)
      expect(quota).not_to be_valid
      expect(quota.errors[:used_bytes]).to include('must be greater than or equal to 0')
    end

    it 'accepts 0' do
      expect(build(:storage_quota, used_bytes: 0)).to be_valid
    end

    it 'accepts a used value above the quota (over-quota rows are storable)' do
      expect(build(:storage_quota, quota_bytes: 10, used_bytes: 11)).to be_valid
    end
  end

  describe 'tier validation' do
    described_class::TIERS.each do |tier|
      it "accepts the #{tier} tier" do
        expect(build(:storage_quota, tier: tier)).to be_valid
      end
    end

    it 'rejects an unknown tier' do
      quota = build(:storage_quota, tier: 'platinum')
      expect(quota).not_to be_valid
      expect(quota.errors[:tier]).to include('is not included in the list')
    end

    it 'rejects a blank tier' do
      quota = build(:storage_quota, tier: '')
      expect(quota).not_to be_valid
      expect(quota.errors[:tier]).to include("can't be blank")
    end

    it 'declares a limit for every tier' do
      expect(described_class::TIER_LIMITS.keys).to match_array(described_class::TIERS)
    end
  end

  describe 'uniqueness of user_id' do
    it 'rejects a second quota for the same user' do
      existing = create(:storage_quota)
      duplicate = build(:storage_quota, user_id: existing.user_id)
      expect(duplicate).not_to be_valid
      expect(duplicate.errors[:user_id]).to include('has already been taken')
    end
  end
end
