class SystemConfigSerializer < ActiveModel::Serializer
  attributes :id, :key, :value, :value_type, :description, :is_secret,
             :created_at, :updated_at

  def key
    object.config_key
  end

  def value
    object.is_secret ? '********' : object.value
  end
end
