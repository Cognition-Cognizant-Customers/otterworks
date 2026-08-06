require 'rails_helper'

# Boundary coverage for the storage-quota arithmetic (billing-relevant path).
# The comparison under test is `used_bytes >= quota_bytes`, expressed twice:
# once in the `over_quota?` predicate and once in the `over_quota` SQL scope.
RSpec.describe StorageQuota do
  let(:quota_bytes) { 1_000 }

  def build_quota(used)
    build(:storage_quota, quota_bytes: quota_bytes, used_bytes: used)
  end

  describe '#over_quota?' do
    it 'over_quota?_used_one_byte_below_quota_returns_false' do
      expect(build_quota(quota_bytes - 1).over_quota?).to be(false)
    end

    it 'over_quota?_used_exactly_equal_to_quota_returns_true' do
      expect(build_quota(quota_bytes).over_quota?).to be(true)
    end

    it 'over_quota?_used_one_byte_above_quota_returns_true' do
      expect(build_quota(quota_bytes + 1).over_quota?).to be(true)
    end
  end

  describe '.over_quota scope' do
    let!(:below) { create(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes - 1) }
    let!(:equal) { create(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes) }
    let!(:above) { create(:storage_quota, quota_bytes: quota_bytes, used_bytes: quota_bytes + 1) }

    it 'over_quota_scope_used_one_byte_below_quota_excludes_record' do
      expect(described_class.over_quota).not_to include(below)
    end

    it 'over_quota_scope_used_exactly_equal_to_quota_includes_record' do
      expect(described_class.over_quota).to include(equal)
    end

    it 'over_quota_scope_used_one_byte_above_quota_includes_record' do
      expect(described_class.over_quota).to include(above)
    end

    it 'over_quota_scope_and_predicate_agree_on_every_boundary_record' do
      selected_by_scope = described_class.over_quota.pluck(:id)
      selected_by_predicate = [below, equal, above].select(&:over_quota?).map(&:id)

      expect(selected_by_scope).to match_array(selected_by_predicate)
    end
  end

  describe 'quota_bytes numericality' do
    it 'quota_bytes_zero_is_invalid' do
      quota = build(:storage_quota, quota_bytes: 0)

      expect(quota).to be_invalid
      expect(quota.errors[:quota_bytes]).to include('must be greater than 0')
    end

    it 'quota_bytes_one_is_valid' do
      expect(build(:storage_quota, quota_bytes: 1, used_bytes: 0)).to be_valid
    end

    it 'quota_bytes_negative_is_invalid' do
      quota = build(:storage_quota, quota_bytes: -1)

      expect(quota).to be_invalid
      expect(quota.errors[:quota_bytes]).to include('must be greater than 0')
    end

    it 'quota_bytes_nil_is_invalid' do
      quota = build(:storage_quota, quota_bytes: nil)

      expect(quota).to be_invalid
      expect(quota.errors[:quota_bytes]).to include("can't be blank")
    end
  end

  describe '#usage_percentage' do
    it 'usage_percentage_quota_bytes_zero_returns_zero_without_raising' do
      quota = build(:storage_quota, quota_bytes: 0, used_bytes: 500)

      expect { quota.usage_percentage }.not_to raise_error
      expect(quota.usage_percentage).to eq(0)
    end

    it 'usage_percentage_non_terminating_division_rounds_to_two_decimal_places' do
      expect(build(:storage_quota, quota_bytes: 3, used_bytes: 1).usage_percentage).to eq(33.33)
    end

    it 'usage_percentage_used_equal_to_quota_returns_one_hundred' do
      expect(build_quota(quota_bytes).usage_percentage).to eq(100.0)
    end

    it 'usage_percentage_used_above_quota_exceeds_one_hundred' do
      expect(build(:storage_quota, quota_bytes: 100, used_bytes: 250).usage_percentage).to eq(250.0)
    end
  end

  describe '#remaining_bytes' do
    it 'remaining_bytes_used_one_byte_below_quota_returns_one' do
      expect(build_quota(quota_bytes - 1).remaining_bytes).to eq(1)
    end

    it 'remaining_bytes_used_exactly_equal_to_quota_returns_zero' do
      expect(build_quota(quota_bytes).remaining_bytes).to eq(0)
    end

    it 'remaining_bytes_used_above_quota_clamps_at_zero' do
      expect(build_quota(quota_bytes + 5_000).remaining_bytes).to eq(0)
    end
  end
end
