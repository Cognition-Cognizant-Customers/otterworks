require 'rails_helper'

# Precedence and boundary behaviour of FeatureFlag#enabled_for_user?.
# The model has three inputs that can decide a flag: the global `enabled` switch,
# the per-user `target_users` override and the percentage rollout bucket.
RSpec.describe FeatureFlag do
  # Mirrors the bucketing in FeatureFlag#enabled_for_user? so the rollout
  # boundaries can be pinned without depending on a lucky uuid.
  def bucket_for(flag_name, user_id)
    # nosemgrep: ruby.lang.security.weak-hashes-md5.weak-hashes-md5
    Digest::MD5.hexdigest("#{flag_name}:#{user_id}").hex % 100
  end

  # Deterministic search for a user id that lands in a chosen rollout bucket.
  def user_id_in_bucket(flag_name, bucket)
    candidate = (0..10_000).lazy.map { |i| "user-#{i}" }.find { |id| bucket_for(flag_name, id) == bucket }
    raise "no candidate id found for bucket #{bucket}" if candidate.nil?

    candidate
  end

  let(:flag_name) { 'precedence_flag' }
  let(:user_id) { 'user-under-test' }

  describe 'precedence between the global switch, the per-user override and the rollout' do
    it 'per-user override wins over a 0% rollout' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_users: [user_id])
      expect(flag.enabled_for_user?(user_id)).to be true
    end

    it 'the global off switch wins over a per-user override' do
      flag = build(:feature_flag, name: flag_name, enabled: false, rollout_percentage: 100,
                                  target_users: [user_id])
      expect(flag.enabled_for_user?(user_id)).to be false
    end

    it 'the global off switch wins over a 100% rollout' do
      flag = build(:feature_flag, name: flag_name, enabled: false, rollout_percentage: 100)
      expect(flag.enabled_for_user?(user_id)).to be false
    end

    it 'expiry wins over a per-user override' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 100,
                                  target_users: [user_id], expires_at: 1.hour.ago)
      expect(flag.enabled_for_user?(user_id)).to be false
    end

    it 'a not-yet-expired flag still honours the per-user override' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_users: [user_id], expires_at: 1.hour.from_now)
      expect(flag.enabled_for_user?(user_id)).to be true
    end

    it 'a user outside target_users falls through to the rollout' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_users: ['someone-else'])
      expect(flag.enabled_for_user?(user_id)).to be false
    end

    it 'ignores target_groups entirely — group membership grants nothing' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0,
                                  target_groups: ['beta-testers'], target_users: [])
      expect(flag.enabled_for_user?(user_id)).to be false
    end
  end

  describe 'rollout bucket boundary trio' do
    let(:bucket) { 42 }
    let(:bucketed_user) { user_id_in_bucket(flag_name, bucket) }

    it 'excludes a user whose bucket is one above the rollout percentage' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: bucket - 1)
      expect(flag.enabled_for_user?(bucketed_user)).to be false
    end

    it 'excludes a user whose bucket equals the rollout percentage (comparison is <, not <=)' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: bucket)
      expect(flag.enabled_for_user?(bucketed_user)).to be false
    end

    it 'includes a user whose bucket is one below the rollout percentage' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: bucket + 1)
      expect(flag.enabled_for_user?(bucketed_user)).to be true
    end

    it 'excludes everyone at 0% rollout, including the bucket-0 user' do
      zero_bucket_user = user_id_in_bucket(flag_name, 0)
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 0)

      expect(flag.enabled_for_user?(zero_bucket_user)).to be false
    end

    it 'includes the highest bucket (99) only at 100% rollout' do
      top_bucket_user = user_id_in_bucket(flag_name, 99)
      just_below = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 99)
      full = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 100)

      expect(just_below.enabled_for_user?(top_bucket_user)).to be false
      expect(full.enabled_for_user?(top_bucket_user)).to be true
    end

    it 'is stable across repeated evaluations for the same user' do
      flag = build(:feature_flag, name: flag_name, enabled: true, rollout_percentage: 50)
      results = Array.new(5) { flag.enabled_for_user?(user_id) }

      expect(results.uniq.size).to eq(1)
    end
  end

  describe 'rollout_percentage validation boundaries' do
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

    it 'rejects a fractional percentage' do
      flag = build(:feature_flag, rollout_percentage: 50.5)
      expect(flag).not_to be_valid
      expect(flag.errors[:rollout_percentage]).to include('must be an integer')
    end

    it 'rejects nil' do
      flag = build(:feature_flag, rollout_percentage: nil)
      expect(flag).not_to be_valid
      expect(flag.errors[:rollout_percentage]).to include('is not a number')
    end
  end

  describe 'unset flags' do
    it 'has no record at all for a name that was never created' do
      expect(described_class.find_by(name: 'never_created_flag')).to be_nil
    end

    it 'defaults a brand new flag to off for every user' do
      flag = described_class.new(name: 'brand_new_flag')

      expect(flag.enabled).to be false
      expect(flag.rollout_percentage).to eq(0)
      expect(flag.enabled_for_user?(user_id)).to be false
    end

    it 'omits unset flags from the enabled scope' do
      created = create(:feature_flag, name: 'created_but_off')

      expect(described_class.enabled).not_to include(created)
    end
  end

  describe 'non-boolean values for enabled' do
    it 'treats a recognised truthy string as enabled' do
      flag = build(:feature_flag, enabled: 'yes', rollout_percentage: 100)
      expect(flag.enabled).to be true
      expect(flag.enabled_for_user?(user_id)).to be true
    end

    it 'treats a recognised falsey string as disabled' do
      expect(build(:feature_flag, enabled: '0').enabled).to be false
      expect(build(:feature_flag, enabled: 'off').enabled).to be false
    end

    # Rails' boolean cast maps anything outside its FALSE_VALUES set to true, so a
    # typo in an API payload silently enables the flag rather than being rejected.
    it 'silently enables the flag for an unrecognised string (fail-open cast)' do
      flag = build(:feature_flag, enabled: 'maybe', rollout_percentage: 100)

      expect(flag.enabled).to be true
      expect(flag).to be_valid
      expect(flag.enabled_for_user?(user_id)).to be true
    end

    it 'casts nil to nil and fails at the database NOT NULL constraint, not in validation' do
      flag = build(:feature_flag, enabled: nil)

      expect(flag).to be_valid
      expect { flag.save! }.to raise_error(ActiveRecord::NotNullViolation)
    end
  end

  describe 'expiry boundaries' do
    it 'treats a flag expiring in the past as expired' do
      expect(build(:feature_flag, expires_at: 1.second.ago)).to be_expired
    end

    it 'treats a flag expiring in the future as live' do
      expect(build(:feature_flag, expires_at: 1.second.from_now)).not_to be_expired
    end

    it 'excludes an expired flag from the active scope and keeps a live one' do
      expired = create(:feature_flag, expires_at: 1.hour.ago)
      live = create(:feature_flag, expires_at: 1.hour.from_now)
      never_expires = create(:feature_flag, expires_at: nil)

      active = described_class.active
      expect(active).to include(live, never_expires)
      expect(active).not_to include(expired)
    end
  end
end
