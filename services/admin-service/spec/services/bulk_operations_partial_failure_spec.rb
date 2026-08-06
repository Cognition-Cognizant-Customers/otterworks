require 'rails_helper'

# Partial-failure, batch-size and idempotency behaviour of BulkOperationsService.
# Complements spec/services/bulk_operations_service_spec.rb, which covers the happy paths.
RSpec.describe BulkOperationsService do
  # BulkOperationsService iterates with `find_each`, i.e. ordered by primary key,
  # so "item 3 of 5" is the third user by uuid, not by creation order.
  def ordered_users(count)
    create_list(:admin_user, count).sort_by(&:id)
  end

  # Makes a persisted user fail on its next validated save without touching
  # production code: the stored email no longer matches the format validation.
  def poison(user)
    user.update_column(:email, "not-an-email-#{user.id}")
    user
  end

  describe 'partial failure inside a batch' do
    let(:users) { ordered_users(5) }
    let(:third) { users[2] }

    before { poison(third) }

    it 'reports four successes and one failure' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(result.success_count).to eq(4)
      expect(result.failure_count).to eq(1)
    end

    it 'commits the items processed before the failing one (no batch rollback)' do
      described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(users[0].reload.status).to eq('suspended')
      expect(users[1].reload.status).to eq('suspended')
    end

    it 'commits the items processed after the failing one as well' do
      described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(users[3].reload.status).to eq('suspended')
      expect(users[4].reload.status).to eq('suspended')
    end

    it 'leaves the failing item untouched' do
      described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(third.reload.status).to eq('active')
    end

    it 'reports per-item status for the failure, identified by user_id' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(result.errors.size).to eq(1)
      expect(result.errors.first[:user_id]).to eq(third.id)
      expect(result.errors.first[:error]).to match(/Email is invalid/)
    end

    it 'does not report per-item status for the successes (only aggregate counts)' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(result.to_h.keys).to contain_exactly(:success_count, :failure_count, :errors)
      expect(result.errors.filter_map { |e| e[:user_id] }).to contain_exactly(third.id)
    end

    it 'still writes a single audit log recording the mixed outcome' do
      expect do
        described_class.process(operation: 'suspend', user_ids: users.map(&:id))
      end.to change { AuditLog.by_action('bulk.users_updated').count }.by(1)

      log = AuditLog.by_action('bulk.users_updated').last
      expect(log.changes_made['success']).to eq(4)
      expect(log.changes_made['failures']).to eq(1)
    end
  end

  describe 'every item fails' do
    let(:users) { ordered_users(3) }

    before { users.each { |u| poison(u) } }

    it 'reports zero successes and counts each item as a failure' do
      result = described_class.process(operation: 'suspend', user_ids: users.map(&:id))

      expect(result.success_count).to eq(0)
      expect(result.failure_count).to eq(3)
      expect(result.errors.size).to eq(3)
    end
  end

  describe 'batch size boundaries' do
    it 'accepts an empty batch and reports nothing done' do
      result = described_class.process(operation: 'suspend', user_ids: [])

      expect(result.success_count).to eq(0)
      expect(result.failure_count).to eq(0)
      expect(result.errors).to be_empty
    end

    it 'processes a batch of exactly one' do
      user = create(:admin_user)
      result = described_class.process(operation: 'suspend', user_ids: [user.id])

      expect(result.success_count).to eq(1)
      expect(user.reload.status).to eq('suspended')
    end

    context 'around the 100-item pagination cap used elsewhere in the service' do
      let(:users) { create_list(:admin_user, 101) }
      let(:ids) { users.map(&:id) }

      it 'processes 99 items' do
        result = described_class.process(operation: 'suspend', user_ids: ids.first(99))
        expect(result.success_count).to eq(99)
      end

      it 'processes exactly 100 items' do
        result = described_class.process(operation: 'suspend', user_ids: ids.first(100))
        expect(result.success_count).to eq(100)
      end

      # No documented batch cap exists: the 100-row cap in ApplicationController#paginate
      # applies to listing only, so 101 items are accepted here.
      it 'processes 101 items — no batch size cap is enforced' do
        result = described_class.process(operation: 'suspend', user_ids: ids)
        expect(result.success_count).to eq(101)
        expect(result.failure_count).to eq(0)
      end
    end
  end

  describe 'unknown and duplicate ids' do
    it 'counts unknown ids as failures with a single aggregated error' do
      result = described_class.process(operation: 'suspend', user_ids: [SecureRandom.uuid, SecureRandom.uuid])

      expect(result.success_count).to eq(0)
      expect(result.failure_count).to eq(2)
      expect(result.errors).to contain_exactly({ error: '2 user(s) not found' })
    end

    it 'omits user_id from the not-found error entry' do
      missing = SecureRandom.uuid
      result = described_class.process(operation: 'suspend', user_ids: [missing])

      expect(result.errors.first).not_to have_key(:user_id)
    end

    it 'de-duplicates repeated ids instead of processing them twice' do
      user = create(:admin_user)
      result = described_class.process(operation: 'suspend', user_ids: [user.id, user.id, user.id])

      expect(result.success_count).to eq(1)
      expect(result.failure_count).to eq(0)
    end
  end

  describe 'invalid operations' do
    let(:users) { create_list(:admin_user, 2) }

    it 'rejects an unknown operation without mutating anything' do
      result = described_class.process(operation: 'obliterate', user_ids: users.map(&:id))

      expect(result.errors).to eq(['Invalid operation: obliterate'])
      expect(users.map { |u| u.reload.status }.uniq).to eq(['active'])
    end

    it 'does not write an audit log for an unknown operation' do
      expect do
        described_class.process(operation: 'obliterate', user_ids: users.map(&:id))
      end.not_to change(AuditLog, :count)
    end

    it 'rejects an operation whose name differs only by case' do
      result = described_class.process(operation: 'SUSPEND', user_ids: users.map(&:id))

      expect(result.errors).to eq(['Invalid operation: SUSPEND'])
    end

    it 'fails every item when update_role is given a role outside AdminUser::ROLES' do
      result = described_class.process(operation: 'update_role', user_ids: users.map(&:id),
                                       params: { role: 'overlord' })

      expect(result.success_count).to eq(0)
      expect(result.failure_count).to eq(2)
      expect(users.map { |u| u.reload.role }.uniq).to eq(['viewer'])
    end
  end

  describe 'idempotency' do
    let(:users) { create_list(:admin_user, 3) }
    let(:ids) { users.map(&:id) }

    it 'produces the same counts when the same suspend batch is submitted twice' do
      first = described_class.process(operation: 'suspend', user_ids: ids)
      second = described_class.process(operation: 'suspend', user_ids: ids)

      expect(second.success_count).to eq(first.success_count)
      expect(second.failure_count).to eq(0)
      expect(users.map { |u| u.reload.status }.uniq).to eq(['suspended'])
    end

    it 'leaves users deleted when the same delete batch is submitted twice' do
      described_class.process(operation: 'delete', user_ids: ids)
      described_class.process(operation: 'delete', user_ids: ids)

      expect(users.map { |u| u.reload.status }.uniq).to eq(['deleted'])
    end

    it 'appends one audit log per submission (audit trail is not de-duplicated)' do
      expect do
        2.times { described_class.process(operation: 'suspend', user_ids: ids) }
      end.to change { AuditLog.by_action('bulk.users_updated').count }.by(2)
    end

    it 'overwrites the suspension reason on a repeated suspend with a different reason' do
      described_class.process(operation: 'suspend', user_ids: ids, params: { reason: 'first' })
      described_class.process(operation: 'suspend', user_ids: ids, params: { reason: 'second' })

      expect(users.map { |u| u.reload.suspended_reason }.uniq).to eq(['second'])
    end

    it 'clears the suspension reason when a repeated suspend omits it' do
      described_class.process(operation: 'suspend', user_ids: ids, params: { reason: 'first' })
      described_class.process(operation: 'suspend', user_ids: ids)

      expect(users.map { |u| u.reload.suspended_reason }.uniq).to eq([nil])
    end
  end
end
