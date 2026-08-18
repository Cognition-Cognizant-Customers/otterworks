package com.otterworks.portal.feedback;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.otterworks.portal.common.Json;
import java.util.LinkedHashMap;
import java.util.Map;
import software.amazon.awssdk.services.eventbridge.EventBridgeClient;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequest;
import software.amazon.awssdk.services.eventbridge.model.PutEventsRequestEntry;
import software.amazon.awssdk.services.eventbridge.model.PutEventsResponse;

/**
 * Publishes FeedbackSubmitted to the estate's custom EventBridge bus.
 *
 * <p>Write-then-publish: the caller invokes this only after the DynamoDB write has
 * committed, so the synchronous 201 always reflects committed state. A publish
 * failure is logged and swallowed — it must never fail a committed request; the
 * async recon's events-published==submissions check is the detector for a gap.
 */
public class EventBridgePublisher implements EventPublisher {

    private final EventBridgeClient client;
    private final String busName;

    public EventBridgePublisher(EventBridgeClient client, String busName) {
        this.client = client;
        this.busName = busName;
    }

    @Override
    public void publishSubmitted(Feedback feedback) {
        try {
            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("eventId", FeedbackEvents.eventId(feedback.getId()).toString());
            detail.put("feedbackId", feedback.getId());
            detail.put("userId", feedback.getUserId());
            detail.put("rating", feedback.getRating());
            detail.put("message", feedback.getMessage());
            detail.put("createdAt", feedback.getCreatedAt().toString());

            PutEventsResponse response = client.putEvents(PutEventsRequest.builder()
                    .entries(PutEventsRequestEntry.builder()
                            .eventBusName(busName)
                            .source(FeedbackEvents.SOURCE)
                            .detailType(FeedbackEvents.DETAIL_TYPE)
                            .detail(Json.MAPPER.writeValueAsString(detail))
                            .build())
                    .build());
            if (response.failedEntryCount() != null && response.failedEntryCount() > 0) {
                System.err.println("feedback event publish rejected: "
                        + response.entries().get(0).errorCode());
            }
        } catch (JsonProcessingException | RuntimeException e) {
            System.err.println("feedback event publish failed for id "
                    + feedback.getId() + ": " + e);
        }
    }
}
