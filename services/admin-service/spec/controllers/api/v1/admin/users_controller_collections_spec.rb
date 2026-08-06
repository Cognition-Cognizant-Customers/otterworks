require 'rails_helper'

RSpec.describe Api::V1::Admin::UsersController do
  before { set_jwt_env(request) }

  def body
    response.parsed_body
  end

  def returned_user_ids
    body['users'].pluck('id')
  end

  describe 'GET #index collection sizes' do
    it 'index_with_zero_users_returns_an_empty_collection' do
      get :index

      expect(response).to have_http_status(:ok)
      expect(body['users']).to eq([])
      expect(body['total']).to eq(0)
    end

    it 'index_with_exactly_one_user_returns_that_user' do
      user = create(:admin_user)

      get :index

      expect(returned_user_ids).to eq([user.id])
      expect(body['total']).to eq(1)
    end
  end

  describe 'GET #index page boundaries' do
    let!(:users) { create_list(:admin_user, 5) }

    it 'index_last_full_page_returns_the_requested_page_size' do
      get :index, params: { page: 2, per_page: 2 }

      expect(body['users'].length).to eq(2)
      expect(body['page']).to eq(2)
      expect(body['total']).to eq(5)
    end

    it 'index_final_partial_page_returns_only_the_remaining_records' do
      get :index, params: { page: 3, per_page: 2 }

      expect(body['users'].length).to eq(1)
    end

    it 'index_first_page_beyond_the_last_returns_an_empty_collection_with_the_full_total' do
      get :index, params: { page: 4, per_page: 2 }

      expect(body['users']).to eq([])
      expect(body['total']).to eq(5)
    end

    it 'index_page_zero_is_clamped_to_the_first_page' do
      get :index, params: { page: 0, per_page: 2 }

      expect(body['page']).to eq(1)
      expect(body['users'].length).to eq(2)
    end

    it 'index_per_page_above_the_maximum_is_clamped_to_100' do
      get :index, params: { per_page: 5_000 }

      expect(body['per_page']).to eq(100)
      expect(response.headers['X-Per-Page']).to eq('100')
    end

    it 'index_per_page_zero_is_clamped_to_one' do
      get :index, params: { per_page: 0 }

      expect(body['per_page']).to eq(1)
      expect(body['users'].length).to eq(1)
    end

    it 'index_paginates_without_dropping_or_duplicating_records_across_pages' do
      collected = (1..3).flat_map do |page|
        get :index, params: { page: page, per_page: 2 }
        returned_user_ids
      end

      expect(collected).to match_array(users.map(&:id))
    end

    it 'index_sets_pagination_response_headers' do
      get :index, params: { page: 2, per_page: 2 }

      expect(response.headers['X-Total-Count']).to eq('5')
      expect(response.headers['X-Page']).to eq('2')
    end
  end

  describe 'GET #index filter parameters' do
    let!(:viewer) { create(:admin_user) }
    let!(:admin) { create(:admin_user, :admin) }

    it 'index_absent_role_filter_returns_every_user' do
      get :index

      expect(returned_user_ids).to contain_exactly(viewer.id, admin.id)
    end

    it 'index_empty_string_role_filter_is_ignored_and_returns_every_user' do
      get :index, params: { role: '' }

      expect(returned_user_ids).to contain_exactly(viewer.id, admin.id)
    end

    it 'index_nil_role_filter_is_ignored_and_returns_every_user' do
      get :index, params: { role: nil }

      expect(returned_user_ids).to contain_exactly(viewer.id, admin.id)
    end

    it 'index_present_role_filter_returns_only_matching_users' do
      get :index, params: { role: 'admin' }

      expect(returned_user_ids).to eq([admin.id])
    end

    it 'index_role_filter_matching_nothing_returns_an_empty_collection' do
      get :index, params: { role: 'super_admin' }

      expect(body['users']).to eq([])
      expect(body['total']).to eq(0)
    end

    it 'index_empty_string_search_filter_is_ignored_and_returns_every_user' do
      get :index, params: { q: '' }

      expect(body['total']).to eq(2)
    end

    it 'index_search_filter_with_sql_wildcard_is_escaped_and_matches_nothing' do
      get :index, params: { q: '%' }

      expect(body['users']).to eq([])
    end
  end
end
