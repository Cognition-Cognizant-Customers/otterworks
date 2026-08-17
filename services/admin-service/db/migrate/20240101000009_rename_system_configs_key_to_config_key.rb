class RenameSystemConfigsKeyToConfigKey < ActiveRecord::Migration[7.1]
  def change
    rename_column :system_configs, :key, :config_key
  end
end
