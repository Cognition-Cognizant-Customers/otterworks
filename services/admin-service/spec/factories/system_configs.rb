FactoryBot.define do
  factory :system_config do
    sequence(:config_key) { |n| "config_key_#{n}" }
    value { 'some_value' }
    value_type { 'string' }
    description { Faker::Lorem.sentence }
    is_secret { false }

    trait :integer_config do
      value { '42' }
      value_type { 'integer' }
    end

    trait :boolean_config do
      value { 'true' }
      value_type { 'boolean' }
    end

    trait :json_config do
      value { '{"key": "value"}' }
      value_type { 'json' }
    end

    trait :secret do
      is_secret { true }
    end
  end
end
