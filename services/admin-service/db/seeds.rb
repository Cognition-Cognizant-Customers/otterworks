# Seed development data for admin service

# System configs
SystemConfig.find_or_create_by!(config_key: 'max_upload_size_mb') do |config|
  config.value = '100'
  config.value_type = 'integer'
  config.description = 'Maximum file upload size in megabytes'
end

SystemConfig.find_or_create_by!(config_key: 'maintenance_mode') do |config|
  config.value = 'false'
  config.value_type = 'boolean'
  config.description = 'Enable maintenance mode for the platform'
end

SystemConfig.find_or_create_by!(config_key: 'default_storage_quota_gb') do |config|
  config.value = '5'
  config.value_type = 'integer'
  config.description = 'Default storage quota for new users in GB'
end

SystemConfig.find_or_create_by!(config_key: 'session_timeout_minutes') do |config|
  config.value = '30'
  config.value_type = 'integer'
  config.description = 'Session timeout in minutes'
end

# Feature flags
FeatureFlag.find_or_create_by!(name: 'dark_mode') do |flag|
  flag.description = 'Enable dark mode UI theme'
  flag.enabled = true
  flag.rollout_percentage = 100
end

FeatureFlag.find_or_create_by!(name: 'collaborative_editing') do |flag|
  flag.description = 'Real-time collaborative document editing'
  flag.enabled = true
  flag.rollout_percentage = 80
end

FeatureFlag.find_or_create_by!(name: 'ai_suggestions') do |flag|
  flag.description = 'AI-powered writing suggestions'
  flag.enabled = false
  flag.rollout_percentage = 0
end

Rails.logger.debug 'Seed data loaded successfully'
