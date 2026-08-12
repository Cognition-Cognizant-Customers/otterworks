module Api
  module V1
    module Admin
      class MetricsController < BaseController
        # GET /api/v1/admin/metrics/summary
        def summary
          render json: MetricsAggregator.summary
        end
      end
    end
  end
end
