require 'rails_helper'

# FINDING (documented, not fixed): identity is supplied entirely by the Rack
# middleware in front of the controllers, and no controller re-checks it. When a
# request reaches a controller without JWT metadata — anything that bypasses the
# gateway, or a path added to JwtAuthenticator::EXCLUDED_PATHS — the action still
# executes and the resulting audit record has no actor at all.
RSpec.describe Api::V1::Admin::UsersController do
  let!(:user) { create(:admin_user) }

  it 'admin_users_index_without_jwt_metadata_returns_200' do
    get :index

    expect(response).to have_http_status(:ok)
  end

  it 'admin_users_destroy_without_jwt_metadata_soft_deletes_the_user' do
    delete :destroy, params: { id: user.id }

    expect(response).to have_http_status(:no_content)
    expect(user.reload.status).to eq('deleted')
  end

  it 'admin_users_destroy_without_jwt_metadata_writes_an_audit_log_with_no_actor' do
    delete :destroy, params: { id: user.id }

    log = AuditLog.where(action: 'user.deleted').order(:created_at).last
    expect(log).to be_present
    expect(log.actor_id).to be_nil
    expect(log.actor_email).to be_nil
  end
end
