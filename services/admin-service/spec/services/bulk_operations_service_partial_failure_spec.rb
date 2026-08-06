require 'rails_helper'

# Partial-failure / rollback semantics for bulk user operations (WP-10).
#
# These specs pin *today's* behaviour: `BulkOperationsService` iterates with
# `find_each` and rescues per record, so there is no surrounding transaction —
# a failure part-way through leaves the earlier successes committed. That is a
# deliberate design choice or a latent defect depending on the requirement, and
# either way it should be visible in the suite rather than implied.
RSpec.describe BulkOperationsService do
  # A user whose email is invalid at rest, so any `update!` on it raises
  # ActiveRecord::RecordInvalid. `update_column` bypasses validation, which is
  # how a row like this can exist in the first place.
  def poison_user
    create(:admin_user).tap { |u| u.update_column(:email, "not-an-email-#{u.id}") }
  end

  describe 'partial failure does not roll back the batch' do
    let!(:healthy) { create_list(:admin_user, 2) }
    let!(:broken)  { poison_user }
    let(:user_ids) { (healthy + [broken]).map(&:id) }

    it 'counts the successes' do
      result = described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' })
      expect(result.success_count).to eq(2)
    end

    it 'counts the failure' do
      result = described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' })
      expect(result.failure_count).to eq(1)
    end

    it 'keeps the successful updates committed' do
      described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' })
      expect(healthy.map { |u| u.reload.role }).to all(eq('editor'))
    end

    it 'leaves the failing record untouched' do
      described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' })
      expect(broken.reload.role).to eq('viewer')
    end

    it 'reports the failing user id and message in errors' do
      result = described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' })
      expect(result.errors).to include(hash_including(user_id: broken.id))
    end

    it 'is order independent — the same ids in reverse produce the same counts' do
      forward = described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' })
      reverse = described_class.process(operation: 'update_role', user_ids: user_ids.reverse,
                                        params: { role: 'editor' })
      expect([reverse.success_count, reverse.failure_count])
        .to eq([forward.success_count, forward.failure_count])
    end
  end

  describe 'every record fails' do
    let!(:broken) { Array.new(2) { poison_user } }

    it 'reports zero successes' do
      result = described_class.process(operation: 'update_role', user_ids: broken.map(&:id),
                                       params: { role: 'editor' })
      expect(result.success_count).to eq(0)
    end

    it 'reports one failure per record' do
      result = described_class.process(operation: 'update_role', user_ids: broken.map(&:id),
                                       params: { role: 'editor' })
      expect(result.failure_count).to eq(2)
    end

    it 'collects one error entry per record' do
      result = described_class.process(operation: 'update_role', user_ids: broken.map(&:id),
                                       params: { role: 'editor' })
      expect(result.errors.size).to eq(2)
    end
  end

  describe 'invalid parameters for a valid operation' do
    let!(:users) { create_list(:admin_user, 2) }

    it 'fails every record when the requested role is not a known role' do
      result = described_class.process(operation: 'update_role', user_ids: users.map(&:id),
                                       params: { role: 'overlord' })
      expect(result.failure_count).to eq(2)
    end

    it 'fails every record when no role is supplied at all' do
      result = described_class.process(operation: 'update_role', user_ids: users.map(&:id))
      expect(result.failure_count).to eq(2)
    end

    it 'does not change any role when the role is invalid' do
      described_class.process(operation: 'update_role', user_ids: users.map(&:id), params: { role: 'overlord' })
      expect(users.map { |u| u.reload.role }).to all(eq('viewer'))
    end

    it 'accepts a suspend with no reason' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))
      expect(result.success_count).to eq(2)
      expect(users.first.reload.suspended_reason).to be_nil
    end
  end

  describe 'missing-user accounting boundary' do
    let!(:users) { create_list(:admin_user, 2) }

    it 'reports no failures when every id exists' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))
      expect(result.failure_count).to eq(0)
    end

    it 'reports exactly one failure when one id is missing' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id) + [SecureRandom.uuid])
      expect([result.success_count, result.failure_count]).to eq([2, 1])
    end

    it 'reports every id as a failure when none exist' do
      result = described_class.process(operation: 'suspend', user_ids: Array.new(3) { SecureRandom.uuid })
      expect([result.success_count, result.failure_count]).to eq([0, 3])
    end

    it 'summarises missing ids in a single error entry' do
      result = described_class.process(operation: 'suspend', user_ids: Array.new(3) { SecureRandom.uuid })
      expect(result.errors).to include(hash_including(error: '3 user(s) not found'))
    end

    it 'returns zero counts for an empty id list' do
      result = described_class.process(operation: 'suspend', user_ids: [])
      expect([result.success_count, result.failure_count, result.errors]).to eq([0, 0, []])
    end
  end

  describe 'duplicate ids' do
    let!(:user) { create(:admin_user) }

    it 'applies the operation once per distinct user' do
      result = described_class.process(operation: 'suspend', user_ids: [user.id, user.id, user.id])
      expect(result.success_count).to eq(1)
    end

    it 'does not count deduplicated ids as missing' do
      result = described_class.process(operation: 'suspend', user_ids: [user.id, user.id])
      expect(result.failure_count).to eq(0)
    end

    it 'counts a missing id once even when it is repeated' do
      missing = SecureRandom.uuid
      result = described_class.process(operation: 'suspend', user_ids: [user.id, missing, missing])
      expect(result.failure_count).to eq(1)
    end
  end

  describe 'idempotency of repeated operations' do
    let!(:user) { create(:admin_user) }

    it 'suspending an already-suspended user succeeds again' do
      described_class.process(operation: 'suspend', user_ids: [user.id])
      result = described_class.process(operation: 'suspend', user_ids: [user.id])
      expect([result.success_count, user.reload.status]).to eq([1, 'suspended'])
    end

    it 'activating an already-active user succeeds again' do
      result = described_class.process(operation: 'activate', user_ids: [user.id])
      expect([result.success_count, user.reload.status]).to eq([1, 'active'])
    end

    it 'deleting an already soft-deleted user succeeds again' do
      described_class.process(operation: 'delete', user_ids: [user.id])
      result = described_class.process(operation: 'delete', user_ids: [user.id])
      expect([result.success_count, user.reload.status]).to eq([1, 'deleted'])
    end

    it 'still operates on a soft-deleted user — soft delete is not a tombstone' do
      described_class.process(operation: 'delete', user_ids: [user.id])
      result = described_class.process(operation: 'activate', user_ids: [user.id])
      expect([result.success_count, user.reload.status]).to eq([1, 'active'])
    end
  end

  describe 'operation validation negatives' do
    let!(:user) { create(:admin_user) }

    it 'rejects an unknown operation' do
      result = described_class.process(operation: 'obliterate', user_ids: [user.id])
      expect(result.errors).to eq(['Invalid operation: obliterate'])
    end

    it 'rejects an operation that differs only in case' do
      result = described_class.process(operation: 'SUSPEND', user_ids: [user.id])
      expect(result.errors).to eq(['Invalid operation: SUSPEND'])
    end

    it 'rejects a nil operation' do
      result = described_class.process(operation: nil, user_ids: [user.id])
      expect(result.failure_count).to eq(0)
      expect(result.errors.first).to include('Invalid operation')
    end

    it 'does not touch any record for an invalid operation' do
      described_class.process(operation: 'obliterate', user_ids: [user.id])
      expect(user.reload.status).to eq('active')
    end
  end

  describe 'audit logging' do
    let!(:users) { create_list(:admin_user, 2) }

    it 'records one audit entry for a fully successful batch' do
      expect { described_class.process(operation: 'suspend', user_ids: users.map(&:id)) }
        .to change { AuditLog.by_action('bulk.users_updated').count }.by(1)
    end

    it 'records an audit entry for a partially failed batch too' do
      ids = users.map(&:id) + [SecureRandom.uuid]
      expect { described_class.process(operation: 'suspend', user_ids: ids) }
        .to change { AuditLog.by_action('bulk.users_updated').count }.by(1)
    end

    it 'writes no audit entry for an invalid operation' do
      expect { described_class.process(operation: 'obliterate', user_ids: users.map(&:id)) }
        .not_to(change { AuditLog.count })
    end

    it 'stores the success and failure counts in the audit entry' do
      described_class.process(operation: 'suspend', user_ids: users.map(&:id) + [SecureRandom.uuid])
      entry = AuditLog.by_action('bulk.users_updated').last
      expect(entry.changes_made).to include('success' => 2, 'failures' => 1)
    end

    it 'survives an audit-logging failure without losing the result' do
      allow(AuditLog).to receive(:record!).and_raise(StandardError, 'audit backend down')
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))
      expect(result.success_count).to eq(2)
    end
  end
end
