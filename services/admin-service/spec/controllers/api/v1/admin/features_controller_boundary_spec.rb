require 'rails_helper'

# Feature-flag API boundary coverage (WP-10): rollout percentage limits, name
# validation negatives, and the shared pagination clamp in ApplicationController
# (`per_page` is clamped to 1..100, `page` to >= 1).
RSpec.describe Api::V1::Admin::FeaturesController do
  before { set_jwt_env(request) }

  let(:body) { response.parsed_body }

  def create_flag(attrs)
    post :create, params: { feature: { name: 'boundary_flag' }.merge(attrs) }
  end

  describe 'POST #create rollout_percentage boundary' do
    it 'rejects -1' do
      create_flag(rollout_percentage: -1)
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'accepts 0' do
      create_flag(rollout_percentage: 0)
      expect(response).to have_http_status(:created)
    end

    it 'accepts 100' do
      create_flag(rollout_percentage: 100)
      expect(response).to have_http_status(:created)
    end

    it 'rejects 101' do
      create_flag(rollout_percentage: 101)
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'explains why 101 was rejected' do
      create_flag(rollout_percentage: 101)
      expect(body['details']).to include('Rollout percentage must be less than or equal to 100')
    end

    it 'persists nothing when the percentage is out of range' do
      expect { create_flag(rollout_percentage: 101) }.not_to(change { FeatureFlag.count })
    end
  end

  describe 'PUT #update rollout_percentage boundary' do
    let!(:flag) { create(:feature_flag, rollout_percentage: 50) }

    it 'accepts 99' do
      put :update, params: { id: flag.id, feature: { rollout_percentage: 99 } }
      expect(response).to have_http_status(:ok)
    end

    it 'accepts 100' do
      put :update, params: { id: flag.id, feature: { rollout_percentage: 100 } }
      expect(response).to have_http_status(:ok)
    end

    it 'rejects 101' do
      put :update, params: { id: flag.id, feature: { rollout_percentage: 101 } }
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'leaves the stored percentage unchanged after a rejected update' do
      put :update, params: { id: flag.id, feature: { rollout_percentage: 101 } }
      expect(flag.reload.rollout_percentage).to eq(50)
    end
  end

  describe 'POST #create name negatives' do
    it 'rejects a name that is not snake_case' do
      create_flag(name: 'Not Snake Case')
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'rejects a duplicate name' do
      create(:feature_flag, name: 'already_taken')
      create_flag(name: 'already_taken')
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'rejects a blank name' do
      create_flag(name: '')
      expect(response).to have_http_status(:unprocessable_entity)
    end

    it 'rejects a request with no feature object' do
      post :create
      expect(response).to have_http_status(:bad_request)
    end
  end

  describe 'GET #index pagination boundary' do
    before { create_list(:feature_flag, 3) }

    it 'clamps per_page of 0 up to 1' do
      get :index, params: { per_page: 0 }
      expect(body['per_page']).to eq(1)
    end

    it 'clamps a negative per_page up to 1' do
      get :index, params: { per_page: -5 }
      expect(body['per_page']).to eq(1)
    end

    it 'honours a per_page of 1' do
      get :index, params: { per_page: 1 }
      expect(body['features'].size).to eq(1)
    end

    it 'honours a per_page of 99' do
      get :index, params: { per_page: 99 }
      expect(body['per_page']).to eq(99)
    end

    it 'honours a per_page of exactly 100' do
      get :index, params: { per_page: 100 }
      expect(body['per_page']).to eq(100)
    end

    it 'clamps a per_page of 101 down to 100' do
      get :index, params: { per_page: 101 }
      expect(body['per_page']).to eq(100)
    end

    it 'clamps page 0 up to page 1' do
      get :index, params: { page: 0 }
      expect(body['page']).to eq(1)
    end

    it 'clamps a negative page up to page 1' do
      get :index, params: { page: -3 }
      expect(body['page']).to eq(1)
    end

    it 'returns an empty page beyond the last one' do
      get :index, params: { page: 99, per_page: 2 }
      expect(body['features']).to eq([])
    end

    it 'still reports the full total on an empty page' do
      get :index, params: { page: 99, per_page: 2 }
      expect(body['total']).to eq(3)
    end

    it 'treats a non-numeric page as page 1' do
      get :index, params: { page: 'abc' }
      expect(body['page']).to eq(1)
    end

    it 'publishes the pagination headers' do
      get :index, params: { page: 1, per_page: 2 }
      expect(response.headers.values_at('X-Total-Count', 'X-Page', 'X-Per-Page')).to eq(%w[3 1 2])
    end
  end

  describe 'GET #index filtering' do
    let!(:enabled_flag)  { create(:feature_flag, :enabled) }
    let!(:disabled_flag) { create(:feature_flag) }

    it 'filters to enabled flags' do
      get :index, params: { enabled: 'true' }
      expect(body['features'].pluck('id')).to eq([enabled_flag.id])
    end

    it 'filters to disabled flags' do
      get :index, params: { enabled: 'false' }
      expect(body['features'].pluck('id')).to eq([disabled_flag.id])
    end

    it 'ignores an unrecognised filter value and returns everything' do
      get :index, params: { enabled: 'maybe' }
      expect(body['total']).to eq(2)
    end
  end

  describe 'GET #show / DELETE #destroy negatives' do
    it 'returns 404 for an unknown flag' do
      get :show, params: { id: SecureRandom.uuid }
      expect(response).to have_http_status(:not_found)
    end

    it 'returns 404 when deleting an unknown flag' do
      delete :destroy, params: { id: SecureRandom.uuid }
      expect(response).to have_http_status(:not_found)
    end

    it 'returns 404 for a malformed uuid' do
      get :show, params: { id: 'not-a-uuid' }
      expect(response).to have_http_status(:not_found)
    end
  end
end
