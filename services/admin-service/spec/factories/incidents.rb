FactoryBot.define do
  factory :incident do
    title { 'Storage outage' }
    description { 'Files cannot be uploaded' }
    severity { 'high' }
    status { 'open' }
    affected_service { 'file-service' }
    reporter_id { SecureRandom.uuid }
  end
end
