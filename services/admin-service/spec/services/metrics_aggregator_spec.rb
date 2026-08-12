require 'rails_helper'

RSpec.describe MetricsAggregator do
  it 'calculates user, storage, feature, announcement, and audit metrics' do
    create_list(:admin_user, 2)
    create(:admin_user, :suspended)
    create(:feature_flag, :enabled)
    create(:feature_flag)
    create(:storage_quota, quota_bytes: 100, used_bytes: 50)
    create(:storage_quota, :over_quota, quota_bytes: 100)
    create(:announcement, :published)
    create(:audit_log, action: 'user.updated')
    result = described_class.summary
    expect(result[:users]).to include(total: 3, active: 2, suspended: 1)
    expect(result[:storage]).to include(total_allocated_bytes: 200, total_used_bytes: 6_000_000_050,
                                         average_usage_percent: 3_000_000_025.0, users_over_quota: 1)
    expect(result[:features]).to include(total: 2, enabled: 1, disabled: 1)
    expect(result[:announcements][:active]).to eq(1)
    expect(result[:audit][:total_events]).to eq(1)
  end

  it 'returns zero average usage when no quotas exist' do
    expect(described_class.storage_metrics[:average_usage_percent]).to eq(0)
  end
end
