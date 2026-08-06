require 'rails_helper'

# Boundary coverage for the storage-quota threshold rule (WP-10).
#
# `StorageQuota#over_quota?` and the `.over_quota` scope both use `>=`, so the
# interesting behaviour is at `used_bytes` == `quota_bytes` exactly. Every
# threshold below is exercised as the trio limit-1 / limit / limit+1.
RSpec.describe StorageQuota do
  let(:limit) { 1_000 }

  describe '#over_quota? at the quota boundary' do
    it 'is false one byte below the quota' do
      quota = build(:storage_quota, quota_bytes: limit, used_bytes: limit - 1)
      expect(quota.over_quota?).to be false
    end

    it 'is true exactly at the quota (the rule is >=, not >)' do
      quota = build(:storage_quota, quota_bytes: limit, used_bytes: limit)
      expect(quota.over_quota?).to be true
    end

    it 'is true one byte above the quota' do
      quota = build(:storage_quota, quota_bytes: limit, used_bytes: limit + 1)
      expect(quota.over_quota?).to be true
    end

    it 'is false for a brand-new quota with no usage' do
      quota = build(:storage_quota, quota_bytes: limit, used_bytes: 0)
      expect(quota.over_quota?).to be false
    end
  end

  describe '#remaining_bytes at the quota boundary' do
    it 'returns 1 one byte below the quota' do
      expect(build(:storage_quota, quota_bytes: limit, used_bytes: limit - 1).remaining_bytes).to eq(1)
    end

    it 'returns 0 exactly at the quota' do
      expect(build(:storage_quota, quota_bytes: limit, used_bytes: limit).remaining_bytes).to eq(0)
    end

    it 'clamps to 0 rather than going negative above the quota' do
      expect(build(:storage_quota, quota_bytes: limit, used_bytes: limit + 500).remaining_bytes).to eq(0)
    end
  end

  describe '#usage_percentage at the quota boundary' do
    it 'is just under 100 one byte below the quota' do
      expect(build(:storage_quota, quota_bytes: limit, used_bytes: limit - 1).usage_percentage).to eq(99.9)
    end

    it 'is exactly 100 at the quota' do
      expect(build(:storage_quota, quota_bytes: limit, used_bytes: limit).usage_percentage).to eq(100.0)
    end

    it 'exceeds 100 above the quota (it is not clamped)' do
      expect(build(:storage_quota, quota_bytes: limit, used_bytes: limit + 1).usage_percentage).to eq(100.1)
    end

    it 'rounds to two decimal places' do
      expect(build(:storage_quota, quota_bytes: 3, used_bytes: 1).usage_percentage).to eq(33.33)
    end
  end

  describe 'quota_bytes validation boundary (greater_than: 0)' do
    it 'rejects a negative quota' do
      quota = build(:storage_quota, quota_bytes: -1)
      expect(quota).not_to be_valid
      expect(quota.errors[:quota_bytes]).to include('must be greater than 0')
    end

    it 'rejects a quota of exactly 0' do
      quota = build(:storage_quota, quota_bytes: 0)
      expect(quota).not_to be_valid
      expect(quota.errors[:quota_bytes]).to include('must be greater than 0')
    end

    it 'accepts the smallest legal quota of 1 byte' do
      expect(build(:storage_quota, quota_bytes: 1)).to be_valid
    end

    it 'rejects a nil quota' do
      quota = build(:storage_quota, quota_bytes: nil)
      expect(quota).not_to be_valid
      expect(quota.errors[:quota_bytes]).to include("can't be blank")
    end
  end

  describe 'used_bytes validation boundary (greater_than_or_equal_to: 0)' do
    it 'rejects negative usage' do
      quota = build(:storage_quota, used_bytes: -1)
      expect(quota).not_to be_valid
      expect(quota.errors[:used_bytes]).to include('must be greater than or equal to 0')
    end

    it 'accepts zero usage' do
      expect(build(:storage_quota, used_bytes: 0)).to be_valid
    end

    it 'accepts one byte of usage' do
      expect(build(:storage_quota, used_bytes: 1)).to be_valid
    end
  end

  describe 'a quota of 0 that skipped validation' do
    # quota_bytes: 0 cannot be persisted, but the guard clause in
    # #usage_percentage and the >= in #over_quota? still have to behave.
    let(:quota) { build(:storage_quota, quota_bytes: 0, used_bytes: 0) }

    it 'is rejected by validation' do
      expect(quota).not_to be_valid
    end

    it 'reports 0% usage rather than dividing by zero' do
      expect(quota.usage_percentage).to eq(0)
    end

    it 'still reports over quota, because 0 >= 0' do
      expect(quota.over_quota?).to be true
    end
  end

  describe '.over_quota scope at the boundary' do
    let!(:under)  { create(:storage_quota, quota_bytes: limit, used_bytes: limit - 1) }
    let!(:at)     { create(:storage_quota, quota_bytes: limit, used_bytes: limit) }
    let!(:over)   { create(:storage_quota, quota_bytes: limit, used_bytes: limit + 1) }

    it 'excludes a quota one byte below the limit' do
      expect(described_class.over_quota).not_to include(under)
    end

    it 'includes a quota exactly at the limit' do
      expect(described_class.over_quota).to include(at)
    end

    it 'includes a quota above the limit' do
      expect(described_class.over_quota).to include(over)
    end

    it 'agrees with #over_quota? for every record' do
      described_class.find_each do |record|
        expect(described_class.over_quota.exists?(record.id)).to eq(record.over_quota?)
      end
    end
  end

  describe '.by_tier scope' do
    let!(:free) { create(:storage_quota) }
    let!(:pro)  { create(:storage_quota, :pro) }

    it 'returns only matching tiers' do
      expect(described_class.by_tier('pro')).to contain_exactly(pro)
    end

    it 'returns nothing for a tier that does not exist' do
      expect(described_class.by_tier('platinum')).to be_empty
    end

    it 'does not leak other tiers' do
      expect(described_class.by_tier('free')).not_to include(pro)
    end

    it 'covers every declared tier constant' do
      expect(described_class::TIER_LIMITS.keys).to match_array(described_class::TIERS)
    end
  end

  describe 'tier validation negatives' do
    it 'rejects an unknown tier' do
      quota = build(:storage_quota, tier: 'platinum')
      expect(quota).not_to be_valid
      expect(quota.errors[:tier]).to include('is not included in the list')
    end

    it 'rejects a tier that differs only in case' do
      expect(build(:storage_quota, tier: 'Free')).not_to be_valid
    end

    it 'rejects a blank tier' do
      expect(build(:storage_quota, tier: '')).not_to be_valid
    end
  end

  describe 'uniqueness of user_id' do
    let(:user_id) { SecureRandom.uuid }

    before { create(:storage_quota, user_id: user_id) }

    it 'rejects a second quota for the same user' do
      duplicate = build(:storage_quota, user_id: user_id)
      expect(duplicate).not_to be_valid
      expect(duplicate.errors[:user_id]).to include('has already been taken')
    end

    it 'allows a quota for a different user' do
      expect(build(:storage_quota, user_id: SecureRandom.uuid)).to be_valid
    end
  end

  describe 'bigint column boundary for quota_bytes' do
    let(:bigint_max) { 9_223_372_036_854_775_807 }

    it 'persists a quota one below the bigint maximum' do
      quota = create(:storage_quota, quota_bytes: bigint_max - 1)
      expect(quota.reload.quota_bytes).to eq(bigint_max - 1)
    end

    it 'persists a quota at exactly the bigint maximum' do
      quota = create(:storage_quota, quota_bytes: bigint_max)
      expect(quota.reload.quota_bytes).to eq(bigint_max)
    end

    it 'refuses a quota one above the bigint maximum' do
      expect { create(:storage_quota, quota_bytes: bigint_max + 1) }
        .to raise_error(ActiveModel::RangeError)
    end
  end
end
