class RenameSystemConfigsKeyToConfigKey < ActiveRecord::Migration[7.1]
  def up
    return unless column_exists?(:system_configs, :key)

    rename_column :system_configs, :key, :config_key
  end

  def down
    return unless column_exists?(:system_configs, :config_key)

    rename_column :system_configs, :config_key, :key
  end
end
