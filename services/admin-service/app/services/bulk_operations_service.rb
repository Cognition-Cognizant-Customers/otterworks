class BulkOperationsService
  Result = Struct.new(:success_count, :failure_count, :errors, keyword_init: true)

  VALID_OPERATIONS = %w[suspend activate delete update_role].freeze

  def self.process(operation:, user_ids:, params: {}, request: nil)
    unless VALID_OPERATIONS.include?(operation)
      return Result.new(success_count: 0, failure_count: 0, errors: ["Invalid operation: #{operation}"])
    end

    counts = execute_operations(operation, user_ids, params)
    log_bulk_operation(operation, user_ids, counts, request)

    Result.new(**counts)
  end

  def self.execute_operations(operation, user_ids, params)
    users = AdminUser.where(id: user_ids)
    success_count = 0
    failure_count = 0
    errors = []

    users.find_each do |user|
      apply_operation(user, operation, params)
      success_count += 1
    rescue StandardError => e
      failure_count += 1
      errors << { user_id: user.id, error: e.message }
    end

    missing_count = user_ids.uniq.size - users.size
    if missing_count.positive?
      failure_count += missing_count
      errors << { error: "#{missing_count} user(s) not found" }
    end

    { success_count: success_count, failure_count: failure_count, errors: errors }
  end

  def self.log_bulk_operation(operation, user_ids, counts, request)
    AuditLogger.log(
      action: 'bulk.users_updated',
      resource_type: 'AdminUser',
      request: request,
      changes_made: { operation: operation, user_ids: user_ids, success: counts[:success_count],
                      failures: counts[:failure_count] }
    )
  end

  def self.apply_operation(user, operation, params)
    case operation
    when 'suspend'
      user.suspend!(reason: params[:reason])
    when 'activate'
      user.activate!
    when 'delete'
      user.soft_delete!
    when 'update_role'
      user.update!(role: params[:role])
    else
      raise ArgumentError, "Unsupported operation: #{operation}"
    end
  end

  private_class_method :apply_operation, :execute_operations, :log_bulk_operation
end
