module Api
  module V1
    module Admin
      class BulkController < BaseController
        # POST /api/v1/admin/bulk/users
        def users
          operation = params.require(:operation)
          user_ids = params.require(:user_ids)

          unless user_ids.is_a?(Array) && user_ids.any?
            return render json: { error: 'user_ids must be a non-empty array' }, status: :bad_request
          end

          result = BulkOperationsService.process(
            operation: operation,
            user_ids: user_ids,
            params: bulk_params,
            request: request
          )

          render json: {
            operation: operation,
            success_count: result.success_count,
            failure_count: result.failure_count,
            errors: result.errors
          }, status: bulk_status(result)
        end

        private

        def bulk_status(result)
          if result.errors.any? && result.success_count.zero? && result.failure_count.zero?
            :bad_request
          elsif result.success_count.zero? && result.failure_count.positive?
            :unprocessable_entity
          elsif result.failure_count.zero?
            :ok
          else
            :multi_status
          end
        end

        def bulk_params
          params.permit(:reason, :role).to_h.symbolize_keys # nosemgrep: ruby.lang.security.model-attr-accessible.model-attr-accessible
        end
      end
    end
  end
end
