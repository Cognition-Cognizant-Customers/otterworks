module Api
  module V1
    module Admin
      class AnnouncementsController < BaseController
        before_action :set_announcement, only: %i[show update destroy]

        # GET /api/v1/admin/announcements
        def index
          scope = Announcement.all
          scope = scope.where(status: params[:status]) if params[:status].present?
          scope = scope.by_severity(params[:severity]) if params[:severity].present?
          scope = scope.active if params[:active] == 'true'
          scope = scope.order(created_at: :desc)

          result = paginate(scope)

          render json: {
            announcements: ActiveModelSerializers::SerializableResource.new(result[:records]),
            total: result[:total],
            page: result[:page],
            per_page: result[:per_page]
          }
        end

        # GET /api/v1/admin/announcements/:id
        def show
          render json: @announcement, serializer: AnnouncementSerializer
        end

        # POST /api/v1/admin/announcements
        def create
          announcement = Announcement.new(announcement_params)
          announcement.created_by = current_user_id

          if announcement.save
            AuditLogger.log(
              action: 'announcement.created',
              resource_type: 'Announcement',
              resource_id: announcement.id,
              request: request,
              changes_made: announcement.attributes
            )
            render json: announcement, serializer: AnnouncementSerializer, status: :created
          else
            render json: { error: 'Validation failed', details: announcement.errors.full_messages },
                   status: :unprocessable_entity
          end
        end

        # PUT /api/v1/admin/announcements/:id
        def update
          previous_attributes = @announcement.attributes

          if @announcement.update(announcement_params)
            AuditLogger.log(
              action: 'announcement.updated',
              resource_type: 'Announcement',
              resource_id: @announcement.id,
              request: request,
              changes_made: { before: previous_attributes, after: @announcement.attributes }
            )
            render json: @announcement, serializer: AnnouncementSerializer
          else
            render json: { error: 'Validation failed', details: @announcement.errors.full_messages },
                   status: :unprocessable_entity
          end
        end

        # DELETE /api/v1/admin/announcements/:id
        def destroy
          @announcement.destroy!

          AuditLogger.log(
            action: 'announcement.deleted',
            resource_type: 'Announcement',
            resource_id: @announcement.id,
            request: request
          )

          head :no_content
        end

        private

        def set_announcement
          @announcement = Announcement.find(params[:id]) # nosemgrep: ruby.rails.security.brakeman.check-unscoped-find.check-unscoped-find
        end

        def announcement_params
          params.require(:announcement).permit(:title, :body, :severity, :status, :starts_at, :ends_at,
                                               target_audience: {})
        end
      end
    end
  end
end
