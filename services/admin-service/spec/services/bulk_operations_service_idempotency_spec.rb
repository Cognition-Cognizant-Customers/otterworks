require 'rails_helper'

RSpec.describe BulkOperationsService do
  let!(:users) { create_list(:admin_user, 3) }
  let(:user_ids) { users.map(&:id) }

  describe 'replaying the same operation' do
    it 'process_suspend_replayed_reports_the_same_counts_both_times' do
      first = described_class.process(operation: 'suspend', user_ids: user_ids)
      second = described_class.process(operation: 'suspend', user_ids: user_ids)

      expect([first.success_count, first.failure_count]).to eq([3, 0])
      expect([second.success_count, second.failure_count]).to eq([3, 0])
    end

    it 'process_suspend_replayed_leaves_every_user_suspended' do
      2.times { described_class.process(operation: 'suspend', user_ids: user_ids) }

      expect(users.map { |user| user.reload.status }.uniq).to eq(['suspended'])
    end

    it 'process_update_role_replayed_leaves_every_user_on_the_requested_role' do
      2.times { described_class.process(operation: 'update_role', user_ids: user_ids, params: { role: 'editor' }) }

      expect(users.map { |user| user.reload.role }.uniq).to eq(['editor'])
    end

    it 'process_replayed_writes_one_audit_log_per_invocation' do
      expect do
        2.times { described_class.process(operation: 'suspend', user_ids: user_ids) }
      end.to change { AuditLog.where(action: 'bulk.users_updated').count }.by(2)
    end

    it 'process_suspend_replayed_rewrites_suspended_at_so_the_write_is_not_idempotent' do
      first_run_at = Time.utc(2026, 1, 1, 12, 0, 0)
      replay_at = Time.utc(2026, 1, 2, 12, 0, 0)

      travel_to(first_run_at) { described_class.process(operation: 'suspend', user_ids: user_ids) }
      travel_to(replay_at) { described_class.process(operation: 'suspend', user_ids: user_ids) }

      expect(users.map { |user| user.reload.suspended_at.utc }.uniq).to eq([replay_at])
    end

    it 'process_duplicate_user_ids_in_one_request_counts_each_user_once' do
      result = described_class.process(operation: 'suspend', user_ids: user_ids + user_ids)

      expect(result.success_count).to eq(3)
      expect(result.failure_count).to eq(0)
    end
  end

  describe 'an operation that fails partway through' do
    # `update_column` writes straight to the database, producing a row that is
    # already invalid (display_name is required) before the bulk run starts. Any
    # `update!` on that row therefore raises, exactly as a mid-run failure would.
    let!(:broken_user) { create(:admin_user).tap { |user| user.update_column(:display_name, '') } }

    def run_bulk_update_role
      described_class.process(operation: 'update_role', user_ids: user_ids + [broken_user.id],
                              params: { role: 'editor' })
    end

    it 'process_partial_failure_reports_both_successes_and_failures' do
      result = run_bulk_update_role

      expect(result.success_count).to eq(3)
      expect(result.failure_count).to eq(1)
      expect(result.errors.pluck(:user_id)).to contain_exactly(broken_user.id)
    end

    it 'process_partial_failure_leaves_the_failing_user_on_its_original_role' do
      original_role = broken_user.role

      run_bulk_update_role

      expect(broken_user.reload.role).to eq(original_role)
    end

    it 'process_partial_failure_still_records_a_bulk_audit_log' do
      expect { run_bulk_update_role }.to change { AuditLog.where(action: 'bulk.users_updated').count }.by(1)
    end

    it 'process_partial_failure_keeps_the_already_applied_updates_committed' do
      run_bulk_update_role

      expect(users.map { |user| user.reload.role }.uniq).to eq(['editor'])
    end

    # DEFECT (kept as pending, not fixed): BulkOperationsService.process is not
    # atomic. `execute_operations` iterates with find_each and saves each user in
    # its own transaction, so a failure partway through leaves the earlier users
    # permanently mutated with no rollback and no compensating action — the caller
    # gets a 207 and must reconcile by hand. The example below asserts the
    # behaviour a transactional bulk endpoint would have.
    it 'process_partial_failure_rolls_back_the_already_applied_updates' do
      pending('bulk operations apply per-user with no enclosing transaction; partial writes are never rolled back')

      run_bulk_update_role

      expect(users.map { |user| user.reload.role }.uniq).to eq(['viewer'])
    end
  end
end
