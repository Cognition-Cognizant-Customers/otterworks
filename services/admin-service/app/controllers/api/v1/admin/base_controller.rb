module Api
  module V1
    module Admin
      class BaseController < ApplicationController
        before_action :require_admin_role

        private

        def require_admin_role
          return if current_user_roles.any? do |role|
            %w[admin super_admin].any? { |admin_role| role.casecmp(admin_role).zero? }
          end

          render json: { error: 'Admin role required' }, status: :forbidden
        end

        def current_user_roles
          (Array(request.env['jwt.user_roles']) + Array(current_user_role)).filter_map(&:to_s).uniq
        end
      end
    end
  end
end
