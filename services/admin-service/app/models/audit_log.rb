class AuditLog < ApplicationRecord
  ACTIONS = %w[
    user.created user.updated user.suspended user.activated user.deleted
    feature_flag.created feature_flag.updated feature_flag.deleted
    config.updated announcement.created announcement.updated announcement.deleted
    quota.updated bulk.users_updated
  ].freeze

  Actor = Struct.new(:id, :email, keyword_init: true)
  RequestContext = Struct.new(:ip_address, :user_agent, keyword_init: true)

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

  def self.record!(action:, resource_type:, resource_id: nil, actor: Actor.new,
                   request_context: RequestContext.new, changes_made: {})
    create!(
      action: action,
      resource_type: resource_type,
      resource_id: resource_id,
      actor_id: actor.id,
      actor_email: actor.email,
      changes_made: changes_made,
      ip_address: request_context.ip_address,
      user_agent: request_context.user_agent
    )
  end
end
