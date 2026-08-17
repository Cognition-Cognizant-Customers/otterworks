class AuditLogger
  def self.log(action:, resource_type:, resource_id: nil, request: nil, actor_id: nil, actor_email: nil,
               changes_made: {})
    actor_id ||= request&.env&.dig('jwt.user_id')
    actor_email ||= request&.env&.dig('jwt.user_email')

    AuditLog.record!(
      action: action,
      resource_type: resource_type,
      resource_id: resource_id,
      actor: AuditLog::Actor.new(id: actor_id, email: actor_email),
      request_context: AuditLog::RequestContext.new(
        ip_address: request&.remote_ip,
        user_agent: request&.user_agent
      ),
      changes_made: changes_made
    )
  rescue StandardError => e
    Rails.logger.error("Failed to record audit log: #{e.message}")
  end
end
