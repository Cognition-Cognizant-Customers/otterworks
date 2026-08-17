class AuditLog < ApplicationRecord
  ACTIONS = %w[
    user.created user.updated user.suspended user.activated user.deleted
    feature_flag.created feature_flag.updated feature_flag.deleted
    config.updated announcement.created announcement.updated announcement.deleted
    quota.updated bulk.users_updated
  ].freeze

  validates :action, presence: true
  validates :resource_type, presence: true

  scope :by_action, ->(action) { where(action: action) }
  scope :by_resource, lambda { |type, id = nil|
    scope = where(resource_type: type)
    scope = scope.where(resource_id: id) if id.present?
    scope
  }
  scope :by_actor, ->(actor_id) { where(actor_id: actor_id) }
  scope :recent, -> { order(created_at: :desc) }
  scope :since, ->(time) { where('created_at >= ?', time) }

  RECORDABLE_DETAILS = %i[resource_id actor_id actor_email changes_made ip_address user_agent].freeze

  def self.record!(action:, resource_type:, **details)
    unknown = details.keys - RECORDABLE_DETAILS
    raise ArgumentError, "unknown keyword#{'s' if unknown.many?}: #{unknown.join(', ')}" if unknown.any?

    create!(
      details.reverse_merge(changes_made: {})
             .merge(action: action, resource_type: resource_type)
    )
  end
end
