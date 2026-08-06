require 'rails_helper'

# What happens when the same admin mutation is submitted twice — a retried
# request, a double-clicked button, or an at-least-once delivery from a caller.
RSpec.describe 'Admin mutation idempotency' do
  let(:body) { JSON.parse(response.body) }

  describe Api::V1::Admin::UsersController, type: :controller do
    before { set_jwt_env(request) }

    let!(:user) { create(:admin_user) }

    describe 'PUT #suspend twice' do
      it 'succeeds both times and leaves the user suspended' do
        2.times { put :suspend, params: { id: user.id, reason: 'Policy' } }

        expect(response).to have_http_status(:ok)
        expect(user.reload.status).to eq('suspended')
        expect(user.suspended_reason).to eq('Policy')
      end

      it 'clears the stored reason when the retry omits it' do
        put :suspend, params: { id: user.id, reason: 'Policy' }
        put :suspend, params: { id: user.id }

        expect(user.reload.suspended_reason).to be_nil
      end

      it 'appends one audit entry per submission' do
        expect do
          2.times { put :suspend, params: { id: user.id, reason: 'Policy' } }
        end.to change { AuditLog.by_action('user.suspended').count }.by(2)
      end
    end

    describe 'PUT #activate twice' do
      it 'succeeds both times and leaves the user active' do
        user.suspend!(reason: 'Policy')
        2.times { put :activate, params: { id: user.id } }

        expect(response).to have_http_status(:ok)
        expect(user.reload.status).to eq('active')
        expect(user.suspended_at).to be_nil
      end
    end

    describe 'DELETE #destroy twice' do
      it 'soft deletes once and stays deleted on the retry' do
        2.times { delete :destroy, params: { id: user.id } }

        expect(response).to have_http_status(:no_content)
        expect(user.reload.status).to eq('deleted')
        expect(AdminUser.where(id: user.id)).to exist
      end
    end

    describe 'PUT #update twice with the same payload' do
      it 'produces the same record state' do
        2.times { put :update, params: { id: user.id, user: { display_name: 'Renamed', role: 'admin' } } }

        expect(response).to have_http_status(:ok)
        expect(user.reload.display_name).to eq('Renamed')
        expect(user.role).to eq('admin')
      end

      it 'rejects a duplicate email on the second, conflicting update' do
        other = create(:admin_user)
        put :update, params: { id: user.id, user: { email: other.email } }

        expect(response).to have_http_status(:unprocessable_entity)
        expect(body['details']).to include('Email has already been taken')
      end

      it 'rejects a role outside AdminUser::ROLES' do
        put :update, params: { id: user.id, user: { role: 'overlord' } }

        expect(response).to have_http_status(:unprocessable_entity)
        expect(user.reload.role).to eq('viewer')
      end

      it 'returns 404 for an unknown user id' do
        put :update, params: { id: SecureRandom.uuid, user: { display_name: 'Ghost' } }

        expect(response).to have_http_status(:not_found)
      end
    end
  end

  describe Api::V1::Admin::FeaturesController, type: :controller do
    before { set_jwt_env(request) }

    describe 'POST #create twice with the same name' do
      let(:params) { { feature: { name: 'duplicate_flag', enabled: true, rollout_percentage: 10 } } }

      it 'creates once and rejects the duplicate' do
        post :create, params: params
        expect(response).to have_http_status(:created)

        post :create, params: params
        expect(response).to have_http_status(:unprocessable_entity)
        expect(body['details']).to include('Name has already been taken')
        expect(FeatureFlag.where(name: 'duplicate_flag').count).to eq(1)
      end

      it 'writes only one creation audit entry' do
        post :create, params: params

        expect do
          post :create, params: params
        end.not_to(change { AuditLog.by_action('feature_flag.created').count })
      end
    end

    describe 'rollout_percentage boundaries through the API' do
      it 'rejects -1' do
        post :create, params: { feature: { name: 'below_range', rollout_percentage: -1 } }

        expect(response).to have_http_status(:unprocessable_entity)
        expect(body['details']).to include('Rollout percentage must be greater than or equal to 0')
      end

      it 'accepts 0' do
        post :create, params: { feature: { name: 'at_zero', rollout_percentage: 0 } }
        expect(response).to have_http_status(:created)
      end

      it 'accepts 100' do
        post :create, params: { feature: { name: 'at_hundred', rollout_percentage: 100 } }
        expect(response).to have_http_status(:created)
      end

      it 'rejects 101' do
        post :create, params: { feature: { name: 'above_range', rollout_percentage: 101 } }

        expect(response).to have_http_status(:unprocessable_entity)
        expect(body['details']).to include('Rollout percentage must be less than or equal to 100')
      end
    end

    describe 'PUT #update twice' do
      let!(:flag) { create(:feature_flag, enabled: false, rollout_percentage: 0) }

      it 'leaves the flag in the same state' do
        2.times { put :update, params: { id: flag.id, feature: { enabled: true, rollout_percentage: 25 } } }

        expect(response).to have_http_status(:ok)
        expect(flag.reload.enabled).to be true
        expect(flag.rollout_percentage).to eq(25)
      end

      # Rails casts any value outside its FALSE_VALUES set to true, so an
      # unrecognised `enabled` value silently switches the flag on.
      it 'enables the flag when given an unrecognised boolean value' do
        put :update, params: { id: flag.id, feature: { enabled: 'maybe' } }

        expect(response).to have_http_status(:ok)
        expect(flag.reload.enabled).to be true
      end
    end

    describe 'DELETE #destroy twice' do
      let!(:flag) { create(:feature_flag) }

      it 'deletes once and 404s on the retry' do
        delete :destroy, params: { id: flag.id }
        expect(response).to have_http_status(:no_content)

        delete :destroy, params: { id: flag.id }
        expect(response).to have_http_status(:not_found)
        expect(FeatureFlag.where(id: flag.id)).not_to exist
      end
    end
  end

  describe Api::V1::Admin::ConfigController, type: :controller do
    before { set_jwt_env(request) }

    let!(:config) { create(:system_config, value: 'original') }

    it 'applies the same value twice without changing the outcome' do
      2.times { put :update, params: { id: config.id, config: { value: 'updated' } } }

      expect(response).to have_http_status(:ok)
      expect(config.reload.value).to eq('updated')
    end

    it 'rejects a blank value' do
      put :update, params: { id: config.id, config: { value: '' } }

      expect(response).to have_http_status(:unprocessable_entity)
      expect(config.reload.value).to eq('original')
    end

    it 'masks a secret config value in the audit trail' do
      secret = create(:system_config, :secret, value: 'super-secret')
      put :update, params: { id: secret.id, config: { value: 'rotated' } }

      log = AuditLog.by_action('config.updated').last
      expect(log.changes_made['before']).to eq('********')
      expect(log.changes_made['after']).to eq('********')
    end
  end
end
