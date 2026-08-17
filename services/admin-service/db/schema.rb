# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[7.1].define(version: 2024_01_01_000008) do
  enable_extension "pgcrypto"
  enable_extension "plpgsql"

  create_table "admin_users", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.string "email", null: false
    t.string "display_name", null: false
    t.string "role", default: "viewer", null: false
    t.string "status", default: "active", null: false
    t.string "avatar_url"
    t.jsonb "metadata", default: {}
    t.datetime "last_login_at"
    t.datetime "suspended_at"
    t.string "suspended_reason"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["email"], name: "index_admin_users_on_email", unique: true
    t.index ["role"], name: "index_admin_users_on_role"
    t.index ["status"], name: "index_admin_users_on_status"
  end

  create_table "announcements", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.string "title", null: false
    t.text "body", null: false
    t.string "severity", default: "info", null: false
    t.string "status", default: "draft", null: false
    t.jsonb "target_audience", default: {}
    t.datetime "starts_at"
    t.datetime "ends_at"
    t.uuid "created_by"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["severity"], name: "index_announcements_on_severity"
    t.index ["starts_at", "ends_at"], name: "index_announcements_on_starts_at_and_ends_at"
    t.index ["status"], name: "index_announcements_on_status"
  end

  create_table "audit_logs", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.uuid "actor_id"
    t.string "actor_email"
    t.string "action", null: false
    t.string "resource_type", null: false
    t.uuid "resource_id"
    t.jsonb "changes_made", default: {}
    t.string "ip_address"
    t.string "user_agent"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["action"], name: "index_audit_logs_on_action"
    t.index ["actor_id"], name: "index_audit_logs_on_actor_id"
    t.index ["created_at"], name: "index_audit_logs_on_created_at"
    t.index ["resource_type", "resource_id"], name: "index_audit_logs_on_resource_type_and_resource_id"
  end

  create_table "feature_flags", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.string "name", null: false
    t.string "description"
    t.boolean "enabled", default: false, null: false
    t.jsonb "target_users", default: []
    t.jsonb "target_groups", default: []
    t.integer "rollout_percentage", default: 0
    t.datetime "expires_at"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["enabled"], name: "index_feature_flags_on_enabled"
    t.index ["name"], name: "index_feature_flags_on_name", unique: true
  end

  create_table "storage_quotas", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.uuid "user_id", null: false
    t.bigint "quota_bytes", default: 5368709120, null: false
    t.bigint "used_bytes", default: 0, null: false
    t.string "tier", default: "free", null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["tier"], name: "index_storage_quotas_on_tier"
    t.index ["user_id"], name: "index_storage_quotas_on_user_id", unique: true
  end

  create_table "incidents", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.string "title", null: false
    t.text "description", null: false
    t.string "severity", default: "medium", null: false
    t.string "status", default: "open", null: false
    t.string "affected_service"
    t.string "devin_session_id"
    t.string "devin_session_url"
    t.string "devin_session_status"
    t.uuid "reporter_id"
    t.datetime "resolved_at"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.datetime "closed_at"
    t.index ["affected_service"], name: "index_incidents_on_affected_service"
    t.index ["devin_session_id"], name: "index_incidents_on_devin_session_id", unique: true
    t.index ["severity"], name: "index_incidents_on_severity"
    t.index ["status"], name: "index_incidents_on_status"
  end

  create_table "system_configs", id: :uuid, default: -> { "gen_random_uuid()" }, force: :cascade do |t|
    t.string "config_key", null: false
    t.text "value", null: false
    t.string "value_type", default: "string", null: false
    t.string "description"
    t.boolean "is_secret", default: false, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["config_key"], name: "index_system_configs_on_config_key", unique: true
  end
end
