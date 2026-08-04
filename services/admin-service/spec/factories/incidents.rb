FactoryBot.define do
  factory :incident do
    title { Faker::Lorem.sentence(word_count: 5) }
    description { Faker::Lorem.paragraph }
    severity { Incident::SEVERITIES.sample }
    status { Incident::STATUSES.sample }
    affected_service { Incident::AFFECTED_SERVICES.sample }
  end
end
