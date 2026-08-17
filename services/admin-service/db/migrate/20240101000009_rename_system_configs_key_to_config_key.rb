class RenameSystemConfigsKeyToConfigKey < ActiveRecord::Migration[7.1]
  # Databases created before 20240101000003 declared :config_key still have the
  # original :key column; fresh databases already have :config_key, so this is a no-op.
  def up
    return unless column_exists?(:system_configs, :key)

    rename_column :system_configs, :key, :config_key
  end

  def down
    return unless column_exists?(:system_configs, :config_key)

    rename_column :system_configs, :config_key, :key
  end
end
