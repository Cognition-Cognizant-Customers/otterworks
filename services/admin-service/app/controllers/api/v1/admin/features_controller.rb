module Api
  module V1
    module Admin
      class FeaturesController < BaseController
        before_action :set_feature_flag, only: %i[show update destroy]

        # GET /api/v1/admin/features
        def index
          scope = FeatureFlag.all
          scope = scope.enabled if params[:enabled] == 'true'
          scope = scope.disabled if params[:enabled] == 'false'
          scope = scope.order(name: :asc)

          result = paginate(scope)

          render json: {
            features: ActiveModelSerializers::SerializableResource.new(result[:records]),
            total: result[:total],
            page: result[:page],
            per_page: result[:per_page]
          }
        end

        # GET /api/v1/admin/features/:id
        def show
          render json: @feature_flag, serializer: FeatureFlagSerializer
        end

        # POST /api/v1/admin/features
        def create
          feature_flag = FeatureFlag.new(feature_flag_params)

          if feature_flag.save
            AuditLogger.log(
              action: 'feature_flag.created',
              resource_type: 'FeatureFlag',
              resource_id: feature_flag.id,
              request: request,
              changes_made: feature_flag.attributes
            )
            render json: feature_flag, serializer: FeatureFlagSerializer, status: :created
          else
            render json: { error: 'Validation failed', details: feature_flag.errors.full_messages },
                   status: :unprocessable_entity
          end
        end

        # PUT /api/v1/admin/features/:id
        def update
          previous_attributes = @feature_flag.attributes

          if @feature_flag.update(feature_flag_params)
            AuditLogger.log(
              action: 'feature_flag.updated',
              resource_type: 'FeatureFlag',
              resource_id: @feature_flag.id,
              request: request,
              changes_made: { before: previous_attributes, after: @feature_flag.attributes }
            )
            render json: @feature_flag, serializer: FeatureFlagSerializer
          else
            render json: { error: 'Validation failed', details: @feature_flag.errors.full_messages },
                   status: :unprocessable_entity
          end
        end

        # DELETE /api/v1/admin/features/:id
        def destroy
          @feature_flag.destroy!

          AuditLogger.log(
            action: 'feature_flag.deleted',
            resource_type: 'FeatureFlag',
            resource_id: @feature_flag.id,
            request: request
          )

          head :no_content
        end

        private

        def set_feature_flag
          @feature_flag = FeatureFlag.find(params[:id]) # nosemgrep: ruby.rails.security.brakeman.check-unscoped-find.check-unscoped-find
        end

        def feature_flag_params
          params.require(:feature).permit(:name, :description, :enabled, :rollout_percentage, :expires_at,
                                          target_users: [], target_groups: [])
        end
      end
    end
  end
end
