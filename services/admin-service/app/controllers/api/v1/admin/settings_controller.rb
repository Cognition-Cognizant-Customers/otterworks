module Api
  module V1
    module Admin
      class SettingsController < BaseController
        # GET /api/v1/admin/settings/auto_investigate
        def auto_investigate
          render json: { enabled: AdminSettingsService.auto_investigate_enabled? }
        end

        # PUT /api/v1/admin/settings/auto_investigate
        def update_auto_investigate
          enabled = ActiveModel::Type::Boolean.new.cast(params[:enabled])
          if enabled.nil?
            return render json: { error: 'Missing required parameter: enabled' }, status: :bad_request
          end
          AdminSettingsService.set_auto_investigate(enabled)
          Rails.logger.info("Auto-investigate toggled to #{enabled}")
          render json: { enabled: AdminSettingsService.auto_investigate_enabled? }
        end
      end
    end
  end
end
