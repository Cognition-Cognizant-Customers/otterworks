require 'rails_helper'

# Feature-flag precedence + boundary coverage (WP-10).
#
# `FeatureFlag#enabled_for_user?` resolves in a fixed order:
#   1. global `enabled` (a disabled flag is off for everyone)
#   2. expiry        (an expired flag is off for everyone)
#   3. per-user targeting (`target_users`)
#   4. rollout percentage — `bucket(user) < rollout_percentage`, bucket derived
#      from MD5("<name>:<user_id>") % 100, so it is stable for a given pair
#   5. default: off
#
# The user ids below are fixed so the MD5 bucket is deterministic; each one is
# asserted against `Digest::MD5` in "bucket fixtures" so a drift in the hashing
# scheme fails loudly instead of silently invalidating the boundary trio.
RSpec.describe FeatureFlag do
  let(:flag_name) { 'rollout_flag' }
  let(:bucket_0_user)  { '00000000-0000-0000-0000-000000000012' }
  let(:bucket_50_user) { '00000000-0000-0000-0000-000000000080' }
  let(:bucket_99_user) { '00000000-0000-0000-0000-000000000175' }
  let(:other_user)     { '00000000-0000-0000-0000-0000000000ff' }

  def bucket_for(user_id)
    # nosemgrep: ruby.lang.security.weak-hashes-md5.weak-hashes-md5
    Digest::MD5.hexdigest("#{flag_name}:#{user_id}").hex % 100
  end

  describe 'bucket fixtures' do
    it 'places bucket_0_user in bucket 0' do
      expect(bucket_for(bucket_0_user)).to eq(0)
    end

    it 'places bucket_50_user in bucket 50' do
      expect(bucket_for(bucket_50_user)).to eq(50)
    end

    it 'places bucket_99_user in bucket 99' do
      expect(bucket_for(bucket_99_user)).to eq(99)
    end

    it 'is stable across repeated calls for the same user' do
      expect(bucket_for(bucket_50_user)).to eq(bucket_for(bucket_50_user))
    end
  end

  describe 'precedence: global disable beats everything' do
    it 'is off for a targeted user when the flag is globally disabled' do
      flag = build(:feature_flag, name: flag_name, enabled: false, target_users: [bucket_50_user])
      expect(flag.enabled_for_user?(bucket_50_user)).to be false
    end

    it 'is off at 100% rollout when the flag is globally disabled' do
      flag = build(:feature_flag, name: flag_name, enabled: false, rollout_percentage: 100)
      expect(flag.enabled_for_user?(bucket_50_user)).to be false
    end
  end

  describe 'precedence: expiry beats per-user targeting and rollout' do
    it 'is off for a targeted user once the flag has expired' do
      flag = build(:feature_flag, name: flag_name, enabled: true, expires_at: 1.day.ago,
                                  target_users: [bucket_50_user])
      expect(flag.enabled_for_user?(bucket_50_user)).to be false
    end

    it 'is off at 100% rollout once the flag has expired' do
      flag = build(:feature_flag, name: flag_name, enabled: true, expires_at: 1.day.ago,
                                  rollout_percentage: 100)
      expect(flag.enabled_for_user?(bucket_50_user)).to be false
    end

    it 'is on for a targeted user while the flag is still live' do
      flag = build(:feature_flag, name: flag_name, enabled: true, expires_at: 1.day.from_now,
                                  target_users: [bucket_50_user])
      expect(flag.enabled_for_user?(bucket_50_user)).to be true
    end
  end

  describe 'precedence: per-user targeting beats the rollout percentage' do
    it 'is on for a targeted user even at 0% rollout' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_users: [bucket_99_user])
      expect(flag.enabled_for_user?(bucket_99_user)).to be true
    end

    it 'is off for a non-targeted user at 0% rollout' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_users: [bucket_99_user])
      expect(flag.enabled_for_user?(other_user)).to be false
    end

    it 'does not treat target_groups as per-user targeting' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_groups: ['admins'])
      expect(flag.enabled_for_user?(bucket_50_user)).to be false
    end

    it 'is off for a nil user id when nothing targets it' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0)
      expect(flag.enabled_for_user?(nil)).to be false
    end
  end

  describe 'rollout boundary for a user in bucket 50 (the rule is bucket < percentage)' do
    def flag_at(percentage)
      build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: percentage)
    end

    it 'is off one percent below the bucket' do
      expect(flag_at(49).enabled_for_user?(bucket_50_user)).to be false
    end

    it 'is off at exactly the bucket number' do
      expect(flag_at(50).enabled_for_user?(bucket_50_user)).to be false
    end

    it 'is on one percent above the bucket' do
      expect(flag_at(51).enabled_for_user?(bucket_50_user)).to be true
    end
  end

  describe 'rollout boundary at the ends of the range' do
    def flag_at(percentage)
      build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: percentage)
    end

    it 'is off at 0% for the lowest possible bucket' do
      expect(flag_at(0).enabled_for_user?(bucket_0_user)).to be false
    end

    it 'is on at 1% for a user in bucket 0' do
      expect(flag_at(1).enabled_for_user?(bucket_0_user)).to be true
    end

    it 'is off at 99% for a user in bucket 99' do
      expect(flag_at(99).enabled_for_user?(bucket_99_user)).to be false
    end

    it 'is on at 100% for a user in bucket 99' do
      expect(flag_at(100).enabled_for_user?(bucket_99_user)).to be true
    end

    it 'is on at 100% even for an expired-free flag with no targeting' do
      expect(flag_at(100).enabled_for_user?(other_user)).to be true
    end
  end

  describe 'rollout_percentage validation boundary (0..100, integers only)' do
    it 'rejects -1' do
      flag = build(:feature_flag, rollout_percentage: -1)
      expect(flag).not_to be_valid
      expect(flag.errors[:rollout_percentage]).to include('must be greater than or equal to 0')
    end

    it 'accepts 0' do
      expect(build(:feature_flag, rollout_percentage: 0)).to be_valid
    end

    it 'accepts 100' do
      expect(build(:feature_flag, rollout_percentage: 100)).to be_valid
    end

    it 'rejects 101' do
      flag = build(:feature_flag, rollout_percentage: 101)
      expect(flag).not_to be_valid
      expect(flag.errors[:rollout_percentage]).to include('must be less than or equal to 100')
    end

    it 'rejects a non-integer percentage' do
      expect(build(:feature_flag, rollout_percentage: 50.5)).not_to be_valid
    end
  end

  describe 'name format negatives' do
    it 'rejects a leading digit' do
      expect(build(:feature_flag, name: '1feature')).not_to be_valid
    end

    it 'rejects kebab-case' do
      expect(build(:feature_flag, name: 'feature-flag')).not_to be_valid
    end

    it 'rejects an uppercase letter' do
      expect(build(:feature_flag, name: 'featureFlag')).not_to be_valid
    end

    it 'rejects a trailing space' do
      expect(build(:feature_flag, name: 'feature ')).not_to be_valid
    end

    it 'accepts a single lowercase letter' do
      expect(build(:feature_flag, name: 'f')).to be_valid
    end

    it 'accepts snake_case with digits' do
      expect(build(:feature_flag, name: 'feature_2_beta')).to be_valid
    end
  end

  describe '#expired? at the expiry instant' do
    let(:now) { Time.utc(2026, 1, 1, 12, 0, 0) }

    around do |example|
      travel_to(now) { example.run }
    end

    it 'is expired one second before now' do
      expect(build(:feature_flag, expires_at: now - 1.second)).to be_expired
    end

    it 'is not expired at exactly now (the rule is expires_at < now)' do
      expect(build(:feature_flag, expires_at: now)).not_to be_expired
    end

    it 'is not expired one second from now' do
      expect(build(:feature_flag, expires_at: now + 1.second)).not_to be_expired
    end

    it 'is not expired when no expiry is set' do
      expect(build(:feature_flag, expires_at: nil)).not_to be_expired
    end
  end

  describe '.active scope at the expiry instant' do
    let(:now) { Time.utc(2026, 1, 1, 12, 0, 0) }

    around do |example|
      travel_to(now) { example.run }
    end

    let!(:just_expired) { create(:feature_flag, expires_at: now - 1.second) }
    let!(:expiring_now) { create(:feature_flag, expires_at: now) }
    let!(:still_live)   { create(:feature_flag, expires_at: now + 1.second) }
    let!(:never_expires) { create(:feature_flag, expires_at: nil) }

    it 'excludes a flag that expired one second ago' do
      expect(described_class.active).not_to include(just_expired)
    end

    it 'excludes a flag expiring at exactly now (the scope is expires_at > now)' do
      expect(described_class.active).not_to include(expiring_now)
    end

    it 'includes a flag expiring one second from now' do
      expect(described_class.active).to include(still_live)
    end

    it 'includes a flag with no expiry' do
      expect(described_class.active).to include(never_expires)
    end

    it 'disagrees with #expired? at exactly the expiry instant' do
      # Pinning a genuine off-by-one between the SQL scope (`> now`) and the
      # Ruby predicate (`< now`): at expires_at == now the record is not
      # `expired?` yet is excluded from `.active`.
      expect(expiring_now.expired?).to be false
      expect(described_class.active).not_to include(expiring_now)
    end
  end
end
