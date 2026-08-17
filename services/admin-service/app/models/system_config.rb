class SystemConfig < ApplicationRecord
  VALUE_TYPES = %w[string integer boolean json].freeze

  validates :config_key, presence: true, uniqueness: true,
                         format: { with: /\A[a-z][a-z0-9_]*\z/, message: 'must be snake_case' }
  validates :value, presence: true
  validates :value_type, presence: true, inclusion: { in: VALUE_TYPES }

  scope :public_configs, -> { where(is_secret: false) }

  def typed_value
    case value_type
    when 'integer' then value.to_i
    when 'boolean' then ActiveModel::Type::Boolean.new.cast(value)
    when 'json' then JSON.parse(value)
    else value
    end
  end
end
