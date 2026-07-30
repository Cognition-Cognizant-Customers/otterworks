require 'rails_helper'

RSpec.describe 'API authentication through the middleware stack', type: :request do
  let(:secret) { 'request-spec-secret' }

  around do |example|
    original = ENV['JWT_SECRET']
    ENV['JWT_SECRET'] = secret
    example.run
    ENV['JWT_SECRET'] = original
  end

  before do
    allow(Rails.application.credentials).to receive(:jwt_secret).and_return(nil)
  end

  describe 'unauthenticated endpoints' do
    it 'serves /health without a token' do
      get '/health'
      expect(response).to have_http_status(:ok)
    end
  end

  describe 'protected endpoints' do
    it 'rejects requests without a token' do
      get '/api/v1/admin/features'
      expect(response).to have_http_status(:unauthorized)
      expect(response.parsed_body['error']).to eq('Missing authorization token')
    end

    it 'rejects requests with an invalid token' do
      get '/api/v1/admin/features', headers: { 'Authorization' => 'Bearer bogus' }
      expect(response).to have_http_status(:unauthorized)
      expect(response.parsed_body['error']).to eq('Invalid or expired token')
    end

    it 'serves requests with a valid token' do
      create(:feature_flag)
      get '/api/v1/admin/features', headers: auth_headers
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['features']).to be_an(Array)
      expect(response.parsed_body['total']).to eq(1)
    end
  end
end
