module Api
  module V1
    module Admin
      class ConfigController < ApplicationController
        before_action :set_config, only: %i[show update]

        # GET /api/v1/admin/config
        def index
          configs = SystemConfig.public_configs.order(config_key: :asc)

          render json: {
            configs: ActiveModelSerializers::SerializableResource.new(configs)
          }
        end

        # GET /api/v1/admin/config/:id
        def show
          render json: @config, serializer: SystemConfigSerializer
        end

        # PUT /api/v1/admin/config/:id
        def update
          previous_value = @config.is_secret ? '********' : @config.value

          if @config.update(config_params)
            after_value = @config.is_secret ? '********' : @config.value
            AuditLogger.log(
              action: 'config.updated',
              resource_type: 'SystemConfig',
              resource_id: @config.id,
              request: request,
              changes_made: { key: @config.config_key, before: previous_value, after: after_value }
            )
            render json: @config, serializer: SystemConfigSerializer
          else
            render json: { error: 'Validation failed', details: @config.errors.full_messages },
                   status: :unprocessable_entity
          end
        end

        private

        def set_config
          @config = SystemConfig.find(params[:id]) # nosemgrep: ruby.rails.security.brakeman.check-unscoped-find.check-unscoped-find
        end

        def config_params
          params.require(:config).permit(:value, :description)
        end
      end
    end
  end
end
