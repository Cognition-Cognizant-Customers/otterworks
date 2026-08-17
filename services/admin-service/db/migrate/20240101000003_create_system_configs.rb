class CreateSystemConfigs < ActiveRecord::Migration[7.1]
  def change
    create_table :system_configs, id: :uuid do |t|
      t.string :key, null: false
      t.text :value, null: false
      t.string :value_type, null: false, default: "string"
      t.string :description
      t.boolean :is_secret, null: false, default: false
      t.timestamps
    end

    add_index :system_configs, :key, unique: true
  end
end
